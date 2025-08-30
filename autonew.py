from flask import Flask, request, jsonify
import requests
import time
import os

app = Flask(__name__)

# -------------------- Config --------------------
INVENTREE_URL = "http://inventree.localhost"
API_TOKEN = "inv-ac6e1c0eac205a44de40bf6469c2531f7c94100f-20250829"
CUSTOMER_ID = int(os.getenv("CUSTOMER_ID", "1"))
CONFIDENCE_THRESHOLD = float(os.getenv("CONFIDENCE_THRESHOLD", "0.80"))

HEADERS = {"Authorization": f"Token {API_TOKEN}"}

# -------------------- Helpers --------------------
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
    candidates = []
    for p in parts:
        stock = get_stock_for_part(p["pk"])
        if user_location:
            stock = [s for s in stock if user_location.lower() in safe_loc_name(s).lower()]
        total_qty = sum(float(s.get("quantity", 0)) for s in stock)
        if total_qty > 0:
            candidates.append({
                "part": p,
                "stock": stock,
                "price": p.get("pricing_min") or 0.0
            })
    return candidates

def cheapest(cands):
    """Return candidate with lowest price and highest stock."""
    return min(
        cands,
        key=lambda c: (c["price"], -sum(float(s.get("quantity",0)) for s in c["stock"]))
    )

# -------------------- Sales Order Helpers --------------------
def create_sales_order(customer_id):
    url = f"{INVENTREE_URL}/api/order/so/"
    payload = {
    "customer": customer_id
}

    r = requests.post(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

def create_sales_order_line(order_id, part_id, quantity):
    url = f"{INVENTREE_URL}/api/order/so-line/"
    payload = {"order": order_id, "part": part_id, "quantity": quantity}
    r = requests.post(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

def allocate_sales_order_line(line_id, stock_item_id, quantity):
    url = f"{INVENTREE_URL}/api/order/so-allocation/"
    payload = {"line": line_id, "item": stock_item_id, "quantity": quantity}
    r = requests.post(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

# Optional: External Order Line
def create_external_order_line(order_id, part_name, quantity, price=None, description=None):
    url = f"{INVENTREE_URL}/api/order/po-extra-line/"
    payload = {
        "order": order_id,
        "description": description or f"Auto-order for {part_name}",
        "quantity": quantity,
        "price": price or "0.0",
        "price_currency": "USD",
        "notes": f"Auto-created from ML allocation",
        "link": "",
        "context": None,
        "order_detail": None,
        "pk": 0,
        "reference": f"ML-{int(time.time())}"
    }
    r = requests.post(url, headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()

# -------------------- Endpoints --------------------
@app.route("/recommend-part", methods=["GET"])
def recommend_part():
    predicted = request.args.get("predicted")
    user_location = request.args.get("location")
    if not predicted:
        return jsonify({"error": "predicted query param is required"}), 400

    parts = get_parts(predicted)
    if not parts:
        return jsonify({"error": "No matching part found"}), 404

    candidates = pick_candidates(parts, user_location)
    if not candidates:
        return jsonify({"error": "No stock found in preferred location"}), 404

    best = cheapest(candidates)
    cheapest_stock = best["stock"][0]

    stock_info = {
        "supplier": cheapest_stock.get("supplier_detail", {}).get("name", "Unknown"),
        "price": cheapest_stock.get("purchase_price"),
        "location": cheapest_stock.get("location_name"),
        "supplier_id": cheapest_stock.get("supplier_part")
    }

    return jsonify({
        "part_name": best["part"]["name"],
        "stock": stock_info,
        "note": f"Cheapest available in {user_location}" if user_location else "Cheapest overall"
    })

@app.route("/predict-and-order", methods=["POST"])
def predict_and_order():
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

    # 1️⃣ Find Candidates
    # parts = get_parts(predicted)
    # if not parts:
    #     return jsonify({"error": "No matching part found"}), 404

    # # 2️⃣ Pick first matching part as predicted part
    # predicted_part = parts[0]  # Use index from part table as "part"
    # part_index = predicted_part["pk"]  # if you really need table index, adjust

    # 3️⃣ Create Sales Order
    so = create_sales_order(
        customer_id=CUSTOMER_ID
    )

    # # 4️⃣ Create Sales Order Line
    # so_line_payload = {
    #     "quantity": qty,
    #     "sale_price": predicted_part.get("pricing_min", 0.0),
    #     "sale_price_currency": "USD",
    #     "target_date": body.get("target_date", "2025-08-31"),
    #     "order": so["pk"],
    #     "part": part_index  # Use part index from part table
    # }

    # so_line = create_sales_order_line(
    #     order_id=so_line_payload["order"],
    #     part_id=so_line_payload["part"],
    #     quantity=so_line_payload["quantity"]
    # )

    # return jsonify({
    #     "status": "ordered",
    #     "prediction": {"predicted": predicted, "location": user_location, "confidence": confidence, "qty": qty},
    #     "sales_order": {"pk": so["pk"], "reference": so.get("reference")},
    #     "sales_order_line": so_line_payload
    # }), 201


# -------------------- Run App --------------------
if __name__ == "__main__":
    app.run(debug=True)
