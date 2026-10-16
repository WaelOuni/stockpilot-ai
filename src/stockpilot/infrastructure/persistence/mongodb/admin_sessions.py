"""Shared local operator sessions on the existing Mongo replica set."""

from datetime import datetime, timezone
from hashlib import sha256
import secrets
import time

from pymongo import ReturnDocument, timeout
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from stockpilot.application.ports.admin_session_store import (
    LoginAttemptsExceeded, Session, SessionCapacityReached, SessionStoreUnavailable,
)


class MongoAdminSessions:
    backend = "mongo"

    def __init__(self, database, *, clock=time.time):
        self.database = database
        self.sessions = database.get_collection(
            "operator_sessions", read_concern=ReadConcern("majority"),
            write_concern=WriteConcern("majority"),
        )
        self.guards = database.get_collection("operator_session_guards", write_concern=WriteConcern("majority"))
        self.clock = clock

    def initialize(self):
        try:
            with timeout(5):
                self.sessions.create_index("expires_at", expireAfterSeconds=0)
                for name in ("capacity", "attempts"):
                    self.guards.update_one({"_id": name}, {"$setOnInsert": {"revision": 0}}, upsert=True)
        except PyMongoError:
            raise SessionStoreUnavailable() from None

    def configure(self, fingerprint: str) -> None:
        # Configuration belongs to each record: worker startup never clears peers.
        pass

    @staticmethod
    def token_hash(token: str) -> str:
        return sha256(token.encode()).hexdigest()

    def _transaction(self, operation):
        try:
            with timeout(5), self.database.client.start_session() as transaction:
                return transaction.with_transaction(
                    operation, read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"), max_commit_time_ms=5000,
                )
        except PyMongoError:
            raise SessionStoreUnavailable() from None

    def _guard(self, name, transaction):
        result = self.guards.find_one_and_update(
            {"_id": name}, {"$inc": {"revision": 1}},
            return_document=ReturnDocument.AFTER, session=transaction,
        )
        if result is None:
            raise SessionStoreUnavailable()
        return result

    def attempt(self, address: str) -> None:
        address_hash = sha256(address.encode()).hexdigest()

        def operation(transaction):
            state = self._guard("attempts", transaction)
            now = self.clock()
            attempts = [entry for entry in state.get("attempts", []) if entry["at"] > now - 60]
            if len(attempts) >= 50 or sum(entry["address"] == address_hash for entry in attempts) >= 5:
                raise LoginAttemptsExceeded()
            attempts.append({"address": address_hash, "at": now})
            self.guards.update_one({"_id": "attempts"}, {"$set": {"attempts": attempts}}, session=transaction)

        self._transaction(operation)

    def get(self, token: str, fingerprint: str) -> Session | None:
        if len(token) != 43:
            return None
        try:
            with timeout(5):
                record = self.sessions.find_one({
                    "_id": self.token_hash(token), "configuration": fingerprint,
                    "expires_at": {"$gt": datetime.fromtimestamp(self.clock(), timezone.utc)},
                })
        except PyMongoError:
            raise SessionStoreUnavailable() from None
        if record is None:
            return None
        return Session(record["username"], record["csrf_token"],
                       record["expires_at"].replace(tzinfo=timezone.utc).timestamp())

    def create(self, username: str, previous: str, ttl: int, capacity: int,
               fingerprint: str) -> tuple[str, Session]:
        # Generate once: automatic transaction retries keep the same opaque result.
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)

        def operation(transaction):
            self._guard("capacity", transaction)
            now = self.clock()
            expiry = datetime.fromtimestamp(now + ttl, timezone.utc)
            expiry = expiry.replace(microsecond=(expiry.microsecond // 1000) * 1000)
            self.sessions.delete_one({"_id": self.token_hash(previous), "configuration": fingerprint},
                                     session=transaction)
            active = self.sessions.count_documents({
                "expires_at": {"$gt": datetime.fromtimestamp(now, timezone.utc)},
            }, session=transaction)
            if active >= capacity:
                raise SessionCapacityReached()
            self.sessions.insert_one({
                "_id": self.token_hash(token), "username": username, "csrf_token": csrf_token,
                "configuration": fingerprint, "expires_at": expiry,
            }, session=transaction)
            return token, Session(username, csrf_token, expiry.timestamp())

        return self._transaction(operation)

    def revoke(self, token: str, fingerprint: str) -> None:
        try:
            # A single-document delete is atomic and immediately visible to peers.
            with timeout(5):
                self.sessions.delete_one({"_id": self.token_hash(token), "configuration": fingerprint})
        except PyMongoError:
            raise SessionStoreUnavailable() from None
