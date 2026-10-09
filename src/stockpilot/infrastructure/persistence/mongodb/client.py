from pymongo import MongoClient


def create_mongo_client(uri: str) -> MongoClient:
    return MongoClient(uri)