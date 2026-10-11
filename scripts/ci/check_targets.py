"""Fail before integration fixtures write outside the dedicated CI endpoints."""
import os
import psycopg
from pymongo import MongoClient
mongo = os.environ["MONGO_TEST_URI"]
postgres = os.environ["POSTGRES_TEST_URI"]
assert mongo == "mongodb://127.0.0.1:27027/?replicaSet=rs0&directConnection=true"
assert postgres == "postgresql://ci:ci@127.0.0.1:55432/stockpilot_ci"
with MongoClient(mongo, serverSelectionTimeoutMS=5000) as client:
    hello = client.admin.command("hello")
    assert hello["setName"] == "rs0" and hello["isWritablePrimary"]
with psycopg.connect(postgres) as connection:
    assert connection.execute("SELECT current_database()").fetchone()[0] == "stockpilot_ci"
print("Verified isolated CI endpoints and writable replica-set primary")
