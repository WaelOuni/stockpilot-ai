"""Shared, short-lived BFF state. Only explicit initialize() creates indexes."""

from datetime import UTC, datetime, timedelta

from bson.codec_options import CodecOptions
from pymongo import ASCENDING, ReturnDocument, timeout
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import ReadPreference
from pymongo.write_concern import WriteConcern

from stockpilot.application.ports.bff_security import (
    BffSession,
    BffUnavailable,
    LoginTransaction,
)
from stockpilot.domain.entities.identity import ExternalIdentity

_WRITE_CONCERN = WriteConcern(w="majority", wtimeout=5000)
_CODEC = CodecOptions(tz_aware=True, tzinfo=UTC)


class MongoBffStore:
    def __init__(self, database):
        self._database = database.with_options(codec_options=_CODEC)
        self._transactions = self._database.get_collection(
            "bff_login_transactions", write_concern=_WRITE_CONCERN
        )
        self._sessions = self._database.get_collection(
            "bff_sessions", write_concern=_WRITE_CONCERN
        )

    def initialize(self) -> None:
        try:
            with timeout(5):
                self._transactions.create_index(
                    [("expires_at", ASCENDING)],
                    expireAfterSeconds=0,
                    name="bff_login_transaction_expiry",
                )
                self._sessions.create_index(
                    [("idle_expires_at", ASCENDING)],
                    expireAfterSeconds=0,
                    name="bff_session_idle_expiry",
                )
                self._sessions.create_index(
                    [("absolute_expires_at", ASCENDING)],
                    expireAfterSeconds=0,
                    name="bff_session_absolute_expiry",
                )
        except PyMongoError:
            raise BffUnavailable("BFF state unavailable") from None

    def put_transaction(self, transaction: LoginTransaction) -> None:
        document = {
            "_id": transaction.state_hash,
            "browser_hash": transaction.browser_hash,
            "nonce": transaction.nonce,
            "verifier": transaction.verifier,
            "organization_id": transaction.organization_id,
            "expires_at": transaction.expires_at,
            "configuration": transaction.configuration,
            "previous_session_hash": transaction.previous_session_hash,
        }
        try:
            with timeout(5):
                self._transactions.insert_one(document)
        except PyMongoError:
            raise BffUnavailable("BFF state unavailable") from None

    def consume_transaction(
        self, state_hash: str, browser_hash: str, configuration: str, now: datetime
    ) -> LoginTransaction | None:
        try:
            with timeout(5):
                document = self._transactions.find_one_and_delete(
                    {
                        "_id": state_hash,
                        "browser_hash": browser_hash,
                        "configuration": configuration,
                        "expires_at": {"$gt": now},
                    }
                )
            if document is None:
                return None
            return LoginTransaction(
                state_hash=document["_id"],
                browser_hash=document["browser_hash"],
                nonce=document["nonce"],
                verifier=document["verifier"],
                organization_id=document["organization_id"],
                expires_at=document["expires_at"],
                configuration=document["configuration"],
                previous_session_hash=document.get("previous_session_hash"),
            )
        except (PyMongoError, KeyError, TypeError, ValueError):
            raise BffUnavailable("BFF state unavailable") from None

    def rotate_session(self, session: BffSession, previous_hash: str | None) -> None:
        document = {
            "_id": session.session_hash,
            "external_identity": {
                "issuer": session.identity.issuer,
                "subject": session.identity.subject,
            },
            "user_id": session.user_id,
            "organization_id": session.organization_id,
            "csrf_token": session.csrf_token,
            "created_at": session.created_at,
            "absolute_expires_at": session.absolute_expires_at,
            "idle_expires_at": session.idle_expires_at,
            "configuration": session.configuration,
        }

        if session.provider_expires_at is not None:
            document.update(provider_expires_at=session.provider_expires_at,
                            refresh_ciphertext=session.refresh_ciphertext,
                            refresh_version=session.refresh_version)

        def rotate(mongo_session):
            self._sessions.insert_one(document.copy(), session=mongo_session)
            if previous_hash is not None:
                self._sessions.delete_one(
                    {"_id": previous_hash, "configuration": session.configuration},
                    session=mongo_session,
                )

        try:
            with timeout(5), self._database.client.start_session() as mongo_session:
                mongo_session.with_transaction(
                    rotate,
                    read_concern=ReadConcern("snapshot"),
                    write_concern=_WRITE_CONCERN,
                    read_preference=ReadPreference.PRIMARY,
                    max_commit_time_ms=5000,
                )
        except PyMongoError:
            raise BffUnavailable("BFF state unavailable") from None

    def get_session(
        self, session_hash: str, configuration: str, now: datetime, idle_seconds: int
    ) -> BffSession | None:
        try:
            with timeout(5):
                document = self._sessions.find_one_and_update(
                    {
                        "_id": session_hash,
                        "configuration": configuration,
                        "absolute_expires_at": {"$gt": now},
                        "idle_expires_at": {"$gt": now},
                    },
                    [
                        {
                            "$set": {
                                "idle_expires_at": {
                                    "$min": [
                                        "$absolute_expires_at",
                                        now + timedelta(seconds=idle_seconds),
                                    ]
                                }
                            }
                        }
                    ],
                    return_document=ReturnDocument.AFTER,
                    upsert=False,
                )
            if document is None:
                return None
            return BffSession(
                session_hash=document["_id"],
                identity=ExternalIdentity(**document["external_identity"]),
                user_id=document["user_id"],
                organization_id=document["organization_id"],
                csrf_token=document["csrf_token"],
                created_at=document["created_at"],
                absolute_expires_at=document["absolute_expires_at"],
                idle_expires_at=document["idle_expires_at"],
                configuration=document["configuration"],
                provider_expires_at=document.get("provider_expires_at"),
                refresh_ciphertext=document.get("refresh_ciphertext"),
                refresh_version=document.get("refresh_version", 0),
            )
        except (PyMongoError, KeyError, TypeError, ValueError):
            raise BffUnavailable("BFF state unavailable") from None

    def revoke_session(self, session_hash: str, configuration: str) -> None:
        try:
            with timeout(5):
                self._sessions.delete_one(
                    {"_id": session_hash, "configuration": configuration}
                )
        except PyMongoError:
            raise BffUnavailable("BFF state unavailable") from None

    def claim_refresh(self, session, owner, now):
        try:
            with timeout(5):
                result = self._sessions.update_one(
                    {"_id": session.session_hash, "configuration": session.configuration,
                     "refresh_version": session.refresh_version, "refresh_owner": {"$exists": False},
                     "absolute_expires_at": {"$gt": now}, "idle_expires_at": {"$gt": now}},
                    {"$set": {"refresh_owner": owner}})
            return result.modified_count == 1
        except PyMongoError:
            raise BffUnavailable("Renewal storage unavailable") from None

    def finish_refresh(self, session, owner, ciphertext, expires, now):
        try:
            with timeout(5):
                result = self._sessions.update_one(
                    {"_id": session.session_hash, "configuration": session.configuration,
                     "refresh_version": session.refresh_version, "refresh_owner": owner,
                     "absolute_expires_at": {"$gt": now}, "idle_expires_at": {"$gt": now}},
                    {"$set": {"refresh_ciphertext": ciphertext, "provider_expires_at": expires},
                     "$inc": {"refresh_version": 1}, "$unset": {"refresh_owner": ""}})
            return result.modified_count == 1
        except PyMongoError:
            raise BffUnavailable("Renewal storage unavailable") from None
