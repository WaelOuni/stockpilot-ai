from pymongo import MongoClient


client = MongoClient("mongodb://localhost:27017")
db = client["stockpilot"]

products = [
    {
        "sku": "BOSCH-X100",
        "name": "Bosch X100",
        "category": "DRILL",
        "price": 129.99,
        "stock": 12,
    },
    {
        "sku": "MAKITA-D200",
        "name": "Makita D200",
        "category": "DRILL",
        "price": 169.99,
        "stock": 0,
    },
    {
        "sku": "BOSCH-S500",
        "name": "Bosch S500",
        "category": "SAW",
        "price": 199.99,
        "stock": 4,
    },
]

collection = db["products"]
collection.delete_many({})
collection.insert_many(products)

print(f"Inserted {len(products)} products")