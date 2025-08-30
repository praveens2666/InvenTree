import requests
import os
import json
import argparse
import datetime
import ast
from typing import List
import difflib

# -------------------- Config --------------------
INVENTREE_URL = os.getenv("INVENTREE_URL", "http://inventree.localhost")
API_TOKEN = os.getenv("INVENTREE_API_TOKEN", "inv-ac6e1c0eac205a44de40bf6469c2531f7c94100f-20250829")
CUSTOMER_ID = int(os.getenv("CUSTOMER_ID", "1"))
HEADERS = {"Authorization": f"Token {API_TOKEN}", "Content-Type": "application/json"}
DEFAULT_LOCATION = os.getenv("USER_LOCATION", "avadi")


# -------------------- HTTP helpers --------------------
def _check_response(r: requests.Response, ctx: str = "request"):
    if r.ok:
        try:
            return r.json()
        except ValueError:
            return r.text
    # try to extract json error message
    try:
        err = r.json()
    except ValueError:
        err = r.text
    raise requests.HTTPError(f"{ctx} failed: {r.status_code} - {err}")


# -------------------- InvenTree helpers --------------------
def get_part_by_name(part_name: str):
    """Search part by name. Tries exact match first, then partial search."""
    url = f"{INVENTREE_URL}/api/part/"
    r = requests.get(url, headers=HEADERS, params={"search": part_name})
    parts = _check_response(r, ctx=f"search part '{part_name}'")
    # exact match
    for p in parts:
        if p.get("name", "").lower() == part_name.lower():
            return p
    # try substring match
    for p in parts:
        if part_name.lower() in p.get("name", "").lower():
            return p
    return None


def get_stock_for_part(part_id: int):
    url = f"{INVENTREE_URL}/api/stock/"
    r = requests.get(url, headers=HEADERS, params={"part": part_id})
    return _check_response(r, ctx=f"get stock for part {part_id}")


def pick_candidates(parts: List[dict], user_location: str = DEFAULT_LOCATION):
    candidates = []
    for p in parts:
        stock = get_stock_for_part(p["pk"])
        if user_location:
            stock = [s for s in stock if user_location.lower() in s.get("location_name", "").lower()]
        total_qty = sum(float(s.get("quantity", 0) or 0) for s in stock)
        if total_qty > 0:
            candidates.append({"part": p, "stock": stock})
    return candidates


def cheapest(cands: List[dict]):
    return min(cands, key=lambda c: min(float(s.get("purchase_price", 0) or 0) for s in c["stock"]))


# -------------------- Sales Order --------------------
def create_sales_order(customer_id: int, description: str = None, target_date: str = None):
    url = f"{INVENTREE_URL}/api/order/so/"
    payload = {"customer": customer_id, "description": description or "Auto-created SO"}
    
        # expected format YYYY-MM-DD
    target_date = "2025-09-05"
    payload["target_date"] = target_date
    
    r = requests.post(url, headers=HEADERS, json=payload)
    return _check_response(r, ctx="create sales order")


def create_sales_order_line(order_id: int, part_id: int, quantity: int = 1):
    url = f"{INVENTREE_URL}/api/order/so-line/"
    payload = {"order": order_id, "part": part_id, "quantity": quantity}
    target_date = "2025-09-05"
    payload["target_date"] = target_date
    r = requests.post(url, headers=HEADERS, json=payload)
    return _check_response(r, ctx="create sales order line")


# -------------------- ML repairs -> SO flow --------------------
def _normalize_repairs(repair_item) -> List[str]:
    """Normalize various ML output formats into a list of part names."""
    if not repair_item:
        return []

    # Helper: markers that mean "no repairs needed"
    NO_REPAIR_MARKERS = {"no repairs needed", "no repair needed", "none", "n/a", "no repairs"}

    # Flatten one level of lists, and handle stringified lists inside
    items = []
    if isinstance(repair_item, list):
        for x in repair_item:
            if isinstance(x, str) and x.strip().startswith("[") and x.strip().endswith("]"):
                try:
                    parsed = ast.literal_eval(x)
                    if isinstance(parsed, (list, tuple)):
                        items.extend(parsed)
                        continue
                except Exception:
                    pass
            items.append(x)
    else:
        items = [repair_item]

    results: List[str] = []
    for it in items:
        # If it's a nested list/tuple, extend
        if isinstance(it, (list, tuple)):
            for v in it:
                if v:
                    results.append(str(v).strip())
            continue

        # Non-string -> stringify
        if not isinstance(it, str):
            if it:
                results.append(str(it).strip())
            continue

        txt = it.strip()
        if not txt:
            continue
        if txt.lower() in NO_REPAIR_MARKERS:
            continue

        # If the element itself is a stringified list, parse it
        if txt.startswith("[") and txt.endswith("]"):
            try:
                parsed = ast.literal_eval(txt)
                if isinstance(parsed, (list, tuple)):
                    for v in parsed:
                        if v:
                            results.append(str(v).strip())
                    continue
            except Exception:
                pass

        # Comma-separated list in a single string
        if "," in txt:
            parts = [p.strip(" '\"") for p in txt.split(",") if p.strip()]
            results.extend(parts)
            continue

        results.append(txt)

    # Final cleanup: remove empty strings and any "no repairs" markers
    final = [r for r in results if r and r.strip().lower() not in NO_REPAIR_MARKERS]
    return final


def process_repairs_for_machine(machine_name: str, repairs) -> dict:
    """Create one sales order for given machine_name and add lines for each repair part.

    repairs may be a string, a list, or an ML output; function normalizes entries.
    Returns a dict with created order info and lists of successes/failures.
    """
    normalized = _normalize_repairs(repairs)
    if not normalized:
        return {"machine": machine_name, "created": False, "reason": "No repairs needed"}

    description = f"Auto SO for {machine_name} - {datetime.datetime.utcnow().isoformat()}"
    so = create_sales_order(CUSTOMER_ID, description=description)
    order_pk = so.get("pk") or so.get("id")
    results = {"machine": machine_name, "created": True, "order": so, "lines": [], "missing_parts": []}

    for item in normalized:
        part_name = item
        part_name = part_name.replace(" ","_").lower()
        part = get_part_by_name(part_name)
        print(part_name.lower())
        
        if not part:
            results["missing_parts"].append(part_name)
            continue
        try:
            line = create_sales_order_line(order_pk, part["pk"], 1)
            results["lines"].append(line)
        except Exception as e:
            results.setdefault("line_errors", []).append({"part": part_name, "error": str(e)})

    return results


def process_ml_output(mapping: dict) -> List[dict]:
    """Process a mapping of machine_name -> repairs and create SOs accordingly."""
    out = []
    for machine, repairs in mapping.items():
        try:
            res = process_repairs_for_machine(machine, repairs)
            out.append(res)
        except Exception as e:
            out.append({"machine": machine, "created": False, "error": str(e)})
    return out


def find_best_part_match(part_name: str):
    """Robust matching for a part name.

    Strategy:
    - normalize the query (underscores -> spaces, lowercasing, strip punctuation)
    - try exact/substring via `get_part_by_name`
    - query the API with the search param and fuzzy-match across name/full_name/description
    - return the closest matching part or None
    """
    import re

    DEBUG = os.getenv("DEBUG_SO", "0") in ("1", "true", "True")

    def _norm(s: str) -> str:
        s2 = s.replace("_", " ")
        s2 = re.sub(r"[^0-9a-zA-Z\s]", " ", s2)
        s2 = re.sub(r"\s+", " ", s2)
        return s2.strip().lower()

    q_raw = str(part_name)
    q = _norm(q_raw)

    if DEBUG:
        print(f"[so.debug] find_best_part_match: raw='{q_raw}' norm='{q}'")

    # quick check using existing helper (handles exact and substring)
    p = get_part_by_name(q_raw)
    if p:
        if DEBUG:
            print(f"[so.debug] exact/substring match via get_part_by_name -> {p.get('name')}")
        return p

    # try searching with the normalized query and a few variants
    candidates = []
    tried_queries = {q_raw, q, q.replace(' ', '+')}
    url = f"{INVENTREE_URL}/api/part/"
    for tq in tried_queries:
        try:
            r = requests.get(url, headers=HEADERS, params={"search": tq})
            cs = _check_response(r, ctx=f"search parts for '{tq}'") or []
            for c in cs:
                if c not in candidates:
                    candidates.append(c)
        except Exception:
            # don't stop on individual query errors
            continue

    if not candidates:
        if DEBUG:
            print("[so.debug] no candidates found from search API")
        return None

    # prepare strings to fuzzy-match against
    choices = []
    part_map = {}
    for c in candidates:
        name = c.get("name", "") or ""
        full = c.get("full_name", "") or ""
        desc = c.get("description", "") or ""
        for s in (name, full, desc):
            if not s:
                continue
            key = _norm(s)
            if key and key not in part_map:
                part_map[key] = c
                choices.append(key)

    # Try direct normalized exact match
    if q in part_map:
        if DEBUG:
            print(f"[so.debug] exact normalized match -> {part_map[q].get('name')}")
        return part_map[q]

    # Use difflib to find close match
    match = difflib.get_close_matches(q, choices, n=1, cutoff=0.55)
    if match:
        found = part_map[match[0]]
        if DEBUG:
            print(f"[so.debug] fuzzy match -> '{match[0]}' -> {found.get('name')}")
        return found

    # fallback: try case-insensitive substring on name fields
    for c in candidates:
        if q in (c.get("name", "").lower() or ""):
            return c

    # last resort: return first candidate
    if DEBUG:
        print(f"[so.debug] returning first candidate -> {candidates[0].get('name')}")
    return candidates[0]


def add_parts_to_existing_so(mapping: dict, quantity: int = 1) -> List[dict]:
    """Expect mapping: {"machine_1": {"order_pk": 18, "missing_parts": [..]}, ...}

    For each entry, attempt to find parts and add them to the provided order PK.
    Returns a list of result dicts per machine.
    """
    results = []
    for machine, info in mapping.items():
        order_pk = info.get("order_pk") or info.get("order") or info.get("order_pk")
        parts = info.get("missing_parts") or info.get("missing") or []
        if not order_pk:
            results.append({"machine": machine, "error": "missing order_pk"})
            continue
        res = {"machine": machine, "order": order_pk, "added": [], "missing": []}
        for pname in parts:
            candidates = _normalize_repairs(pname)
            for candidate in candidates:
                candidate = candidate.strip()
                if not candidate:
                    continue
                part = find_best_part_match(candidate)
                if not part:
                    res["missing"].append(candidate)
                    continue
                try:
                    line = create_sales_order_line(order_pk, part["pk"], quantity)
                    res["added"].append({"requested": candidate, "found": part.get("name"), "line": line})
                except Exception as e:
                    res.setdefault("errors", []).append({"part": candidate, "error": str(e)})
        results.append(res)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create sales orders from ML repair suggestions.")
    parser.add_argument("input", nargs="?", help="JSON file path or '-' to read from stdin. For process mode: {\"machine_1\": repairs, ...}. For apply-mapping mode: {\"machine_1\": {\"order_pk\": 18, \"missing_parts\": [...]}, ...}")
    parser.add_argument("--apply-mapping", action="store_true", help="Treat input as mapping of existing orders to missing_parts and add lines to those orders")
    args = parser.parse_args()

    if not args.input or args.input == "-":
        data = json.load(os.sys.stdin)
    else:
        with open(args.input, "r", encoding="utf-8") as fh:
            data = json.load(fh)

    if args.apply_mapping:
        results = add_parts_to_existing_so(data)
    else:
        results = process_ml_output(data)

    print(json.dumps(results, indent=2))

