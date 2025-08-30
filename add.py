import requests

INVENTREE_URL = "http://inventree.localhost"  # Use localhost with the correct port
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

def create_part(name, ipn, description, price):
    url = f"{INVENTREE_URL}/api/part/"
    data = {
        "name": name,
        "IPN": ipn,
        "description": description,
        "category": 1,
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
    chennai = create_location("india 1 warehouse", "South India hub")
    delhi = create_location("india 2 Warehouse", "North India hub")

    # # 2) Create suppliers
    supplier_a = create_supplier("Supplier D")
    supplier_b = create_supplier("Supplier E")
    supplier_c = create_supplier("Supplier F")

    # 3) Create parts
    filter1 = create_part("bearing", "F123", "Premium bearing", 150)
    motor1  = create_part("motor-X3", "M9003", "High torque motor1", 250)

    # 4) Add stock items
    # Chennai warehouse with two suppliers
    create_stock(filter1, chennai, supplier_a, quantity=10, purchase_price=100)
    create_stock(filter1, chennai, supplier_b, quantity=15, purchase_price=90)

    # Delhi warehouse
    create_stock(filter1, delhi, supplier_c, quantity=20, purchase_price=95)

    # Motor stock
    create_stock(motor1, chennai, supplier_a, quantity=5, purchase_price=250)
