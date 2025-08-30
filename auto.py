from flask import Flask, request, jsonify
import requests
import time
import os

app = Flask(__name__)

INVENTREE_URL = "http://inventree.localhost"
API_TOKEN = "inv-ac6e1c0eac205a44de40bf6469c2531f7c94100f-20250829"

CUSTOMER_ID = int(os.getenv("CUSTOMER_ID", "1"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))

HEADERS = {"Authorization": f"Token {API_TOKEN}"}


# -------------------- helpers --------------------

def safe_loc_name(s):
    return str(
        (s.get("location_name"))
        or (s.get("location_detail", {}) or {}).get("name", "")
        or ""
    )


def get_parts(search):
    url = f"{INVENTREE_URL}/api/part/"
    r = requests.get(url, headers=HEADERS, params={"search": search})
    r.raise_for_status()
    return r.json()


def get_stock_for_part(part_id):
    url = f"{INVENTREE_URL}/api/stock/"
    r = requests.get(url, headers=HEADERS, params={"part": part_id})
    r.raise_for_status()
    return r.json()


def pick_candidates(parts, user_location=None):
    """Return list of candidates with stock filtered by optional location."""
    cands = []
    for p in parts:
        stock = get_stock_for_part(p["pk"])
        if user_location:
            stock = [s for s in stock if user_location.lower() in safe_loc_name(s).lower()]
        total = sum(float(s.get("quantity", 0)) for s in stock)
        if total > 0:
            cands.append({
                "part": p,
                "stock": stock,
                "price": p.get("pricing_min") if p.get("pricing_min") is not None else 0.0
            })
    return cands


def cheapest(cands):
    return min(cands, key=lambda c: (c["price"], -sum(float(s.get("quantity",0)) for s in c["stock"])) )


# -------------------- recommend endpoint --------------------

@app.route("/recommend-part", methods=["GET"])
def recommend_part():
    predicted = request.args.get("predicted")
    user_location = request.args.get("location")

    if not predicted:
        return jsonify({"error": "predicted query param is required"}), 400

    parts = get_parts(predicted)
    if not parts:
        return jsonify({"error": "No matching part found"}), 404

    candidates = []
    for p in parts:
        stock = get_stock_for_part(p["pk"])
        if user_location:
            stock = [s for s in stock if user_location.lower() in s.get("location_name", "").lower()]
        if stock:
            candidates.append({"part": p, "stock": stock})

    if not candidates:
        return jsonify({"error": "No stock found in preferred location"}), 404

    cheapest_stock = None
    best_part = None
    for c in candidates:
        for s in c["stock"]:
            if cheapest_stock is None or s.get("purchase_price", float("inf")) < cheapest_stock.get("purchase_price", float("inf")):
                cheapest_stock = s
                best_part = c["part"]

    stock_info = {
        "supplier": cheapest_stock.get("supplier_detail", {}).get("name", "Unknown"),
        "price": cheapest_stock.get("purchase_price"),
        "location": cheapest_stock.get("location_name"),
        "supplier id": cheapest_stock.get("supplier_part")
    }

    return jsonify({
        "part_name": best_part["name"],
        "stock": stock_info,
        "note": f"Cheapest available in {user_location}" if user_location else "Cheapest overall"
    })


# -------------------- order helpers --------------------

def create_sales_order(customer_id, reference=None, description=None):
    url = f"{INVENTREE_URL}/api/order/so/"
    payload = {
        "customer": customer_id,
        "reference": reference or f"ML-{int(time.time())}",
        "description": description or "Auto-created from ML failure prediction",
    }
    r = requests.post(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def create_sales_order_line(order_id, part_id, quantity):
    url = f"{INVENTREE_URL}/api/order/so-line/"
    payload = {
        "order": order_id,
        "part": part_id,
        "quantity": quantity,
    }
    r = requests.post(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def allocate_sales_order_line(line_id, stock_item_id, quantity):
    url = f"{INVENTREE_URL}/api/order/so-allocation/"
    payload = {
        "line": line_id,
        "item": stock_item_id,
        "quantity": quantity
    }
    r = requests.post(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def create_external_order_line(order_id, part_name, quantity, price=None, description=None, notes=None, link=None, price_currency="USD"):
    """
    Creates an external (PO extra) order line via /api/order/po-extra-line/
    """
    url = f"{INVENTREE_URL}/api/order/po-extra-line/"
    payload = {
        "order": order_id,
        "description": description or f"Auto-order for {part_name}",
        "quantity": quantity,
        "price": price or "0.0",
        "price_currency": price_currency,
        "notes": notes or "",
        "link": link or "",
        "context": None,
        "order_detail": None,
        "pk": 0,
        "reference": f"ML-{int(time.time())}"
    }
    r = requests.post(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


# -------------------- predict and order --------------------

@app.route("/predict-and-order", methods=["POST"])
def predict_and_order():
    """
    Body JSON example:
    {
      "predicted": "filter",
      "location": "Madurai",
      "qty": 1,
      "confidence": 0.92
    }
    """
    body = request.get_json(silent=True) or {}
    predicted = body.get("predicted")
    user_location = body.get("location")
    qty = int(body.get("qty", 1))
    confidence = float(body.get("confidence", 0))

    if not predicted:
        return jsonify({"error": "predicted is required"}), 400
    if confidence < CONFIDENCE_THRESHOLD:
        return jsonify({
            "status": "skipped",
            "reason": f"Confidence {confidence:.2f} below threshold {CONFIDENCE_THRESHOLD:.2f}"
        }), 202

    # 1) Find candidates
    parts = get_parts(predicted)
    cands = pick_candidates(parts, user_location=user_location)
    if not cands:
        return jsonify({"error": "No stock available for predicted part in preferred location"}), 404

    best = cheapest(cands)

    # 2) Choose stock to allocate
    chosen_stock = None
    for s in best["stock"]:
        if float(s.get("quantity", 0)) >= qty:
            chosen_stock = {"pk": s["pk"], "qty": qty, "location": safe_loc_name(s)}
            break
    if not chosen_stock:
        s0 = best["stock"][0]
        chosen_stock = {"pk": s0["pk"], "qty": min(qty, int(s0.get("quantity", 0))), "location": safe_loc_name(s0)}

    # 3) Create Sales Order
    so = create_sales_order(
        customer_id=CUSTOMER_ID,
        description=f"Auto order for predicted failure: {predicted} @ {user_location}"
    )

    # 4) Add line to SO
    line = create_sales_order_line(
        order_id=so["pk"],
        part_id=best["part"]["pk"],
        quantity=qty
    )

    # 5) Allocate stock
    alloc = allocate_sales_order_line(
        line_id=line["pk"],
        stock_item_id=chosen_stock["pk"],
        quantity=chosen_stock["qty"]
    )

    # 6) Create external order line
    external_order = create_external_order_line(
        order_id=so["pk"],
        part_name=best["part"]["name"],
        quantity=qty,
        price=best["part"].get("pricing_min"),
        description=f"External order for {predicted} @ {user_location}",
        notes=f"Allocated from stock item {chosen_stock['pk']}"
    )

    return jsonify({
        "status": "ordered",
        "note": f"Auto-ordered because confidence {confidence:.2f} ≥ threshold {CONFIDENCE_THRESHOLD:.2f}",
        "prediction": {"predicted": predicted, "location": user_location, "confidence": confidence, "qty": qty},
        "selected_part": {
            "pk": best["part"]["pk"],
            "name": best["part"]["name"],
            "price_min": best["part"].get("pricing_min"),
        },
        "stock_source": {
            "stock_item": chosen_stock["pk"],
            "allocated_qty": chosen_stock["qty"],
            "location": chosen_stock["location"]
        },
        "sales_order": {"pk": so["pk"], "reference": so.get("reference")},
        "sales_order_line": {"pk": line["pk"]},
        "allocation": alloc,
        "external_order_line": external_order
    }), 201


if __name__ == "__main__":
    app.run(debug=True)
