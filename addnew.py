import requests

# ------------------- Config -------------------
INVENTREE_URL = "http://inventree.localhost"
API_TOKEN = "inv-ac6e1c0eac205a44de40bf6469c2531f7c94100f-20250829"
HEADERS = {"Authorization": f"Token {API_TOKEN}"}

# ------------------- Helper functions -------------------

def create_supplier(name):
    url = f"{INVENTREE_URL}/api/company/"
    data = {"name": name, "active": True}
    r = requests.post(url, headers=HEADERS, json=data)
    r.raise_for_status()
    print(f"Created supplier: {r.json()}")
    return r.json()["pk"]

def create_location(name, description=""):
    url = f"{INVENTREE_URL}/api/stock/location/"
    data = {"name": name, "description": description, "parent": None}
    r = requests.post(url, headers=HEADERS, json=data)
    r.raise_for_status()
    print(f"Created location: {r.json()}")
    return r.json()["pk"]

def create_part(name,  description, price, category=1):
    url = f"{INVENTREE_URL}/api/part/"
    data = {
        "name": name,
        "description": description,
        "category": category,
        "purchaseable": True,
        "salable": True,
        "component": True,
        "active": True,
        "pricing_min": price,
        "pricing_max": price,
        "units": "pcs"
    }
    r = requests.post(url, headers=HEADERS, json=data)
    r.raise_for_status()
    print(f"Created part: {r.json()}")
    return r.json()["pk"]

def create_stock(part_id, location_id, supplier_id, quantity, purchase_price):
    url = f"{INVENTREE_URL}/api/stock/"
    data = {
        "part": part_id,
        "location": location_id,
        "supplier": supplier_id,
        "quantity": quantity,
        "purchase_price": purchase_price
    }
    r = requests.post(url, headers=HEADERS, json=data)
    r.raise_for_status()
    print(f"Created stock: {r.json()}")
    return r.json()["pk"]

# ------------------- Main -------------------

if __name__ == "__main__":
    # 1) Create locations
    chennai = create_location("avadi", "South India hub")
    delhi = create_location("bihar", "North India hub")

    # 2) Create suppliers
    supplier_a = create_supplier("MRF")
    supplier_b = create_supplier("CEAT")
    supplier_c = create_supplier("CAT")

    # 3) ML-predicted repair parts
    machine_repairs = {
        "machine_4": ["cooling fan", "heat sink", "motor windings", "hydraulic pump"],
        "machine_5": ["Heat exchanger", "Cooling system", "Motor bearings", "Temperature sensor"]
    }

    # 4) Create parts and add stock
    for machine, parts in machine_repairs.items():
        print(f"\n--- Adding parts for {machine} ---")
        for part_name in parts:
            clean_name = part_name.strip().replace(" ", "_").lower()
              # simple unique IPN
            part_id = create_part(
                name=clean_name,
                
                description=f"Auto-added repair part: {part_name}",
                price=150
            )

            # Add stock in Chennai with Supplier A
            create_stock(part_id, chennai, supplier_a, quantity=20, purchase_price=150)

            # Add stock in Delhi with Supplier B
            create_stock(part_id, delhi, supplier_b, quantity=15, purchase_price=145)
