from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

INVENTREE_URL = "http://inventree.localhost"
API_TOKEN = "inv-ac6e1c0eac205a44de40bf6469c2531f7c94100f-20250829"

HEADERS = {"Authorization": f"Token {API_TOKEN}"}

def get_stock_for_part(part_id):
    """Fetch stock items for a given part"""
    url = f"{INVENTREE_URL}/api/stock/?part={part_id}"
    return requests.get(url, headers=HEADERS).json()

@app.route("/recommend-part", methods=["GET"])
def recommend_part():
    predicted = request.args.get("predicted")  # e.g. filter, motor
    user_location = request.args.get("location", None)  # optional

    # 1. Search for parts matching prediction
    parts = requests.get(f"{INVENTREE_URL}/api/part/?search={predicted}", headers=HEADERS).json()
    if not parts:
        return jsonify({"error": "No matching part found"}), 404

    # 2. If only one part → return it directly with stock
    if len(parts) == 1:
        stock = get_stock_for_part(parts[0]["pk"])
        return jsonify({"result": parts[0], "stock": stock, "note": "Only one part found"})

    # 3. Multiple parts → check stock and pricing
    candidates = []
    for part in parts:
        stock_items = get_stock_for_part(part["pk"])
        if not stock_items:
            continue

        # If user_location is provided, filter stock
        if user_location:
            stock_items = [
    s for s in stock_items
    if user_location.lower() in str(s.get("location_name") or s.get("location_detail", {}).get("name", "")).lower()
]


        total_qty = sum(s["quantity"] for s in stock_items)
        if total_qty > 0:
            candidates.append({
                "part": part,
                "stock": stock_items,
                "price": part.get("pricing_min", 0)
            })

    if not candidates:
        return jsonify({"error": "No stock found in preferred location"}), 400

    # 4. Pick cheapest candidate
    best = min(candidates, key=lambda c: c["price"])

    return jsonify({
        "result": best["part"],
        "stock": best["stock"],
        "note": f"Cheapest available in {user_location}" if user_location else "Cheapest overall"
    })

if __name__ == "__main__":
    app.run(debug=True)
