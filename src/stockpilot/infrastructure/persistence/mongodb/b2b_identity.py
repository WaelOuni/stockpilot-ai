"""Read-only adapters for explicitly provisioned B2B identity records."""

from datetime import UTC, datetime

from bson.codec_options import CodecOptions
from pymongo import ASCENDING, timeout
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.read_preferences import ReadPreference
from pymongo.write_concern import WriteConcern

from stockpilot.application.ports.identity_errors import IdentityRepositoryError
from stockpilot.domain.entities.identity import ExternalIdentity, User
from stockpilot.domain.entities.membership import Membership
from stockpilot.domain.entities.organization import Organization
from stockpilot.domain.enums.identity_status import IdentityStatus
from stockpilot.domain.enums.membership_status import MembershipStatus
from stockpilot.domain.enums.organization_status import OrganizationStatus
from stockpilot.domain.enums.role import Role

_CODEC = CodecOptions(tz_aware=True, tzinfo=UTC)
_WRITE_CONCERN = WriteConcern(w="majority", wtimeout=5000)


def _collection(database, name):
    return database.with_options(codec_options=_CODEC).get_collection(
        name,
        write_concern=_WRITE_CONCERN,
        read_concern=ReadConcern("majority"),
        read_preference=ReadPreference.PRIMARY,
    )


def initialize_b2b_indexes(database) -> None:
    """Explicit isolated startup hook; repositories never provision records."""
    try:
        with timeout(5):
            users = _collection(database, "b2b_users")
            users.create_index(
                [("user_id", ASCENDING)], unique=True, name="b2b_user_id"
            )
            users.create_index(
                [
                    ("external_identity.issuer", ASCENDING),
                    ("external_identity.subject", ASCENDING),
                ],
                unique=True,
                name="b2b_external_identity",
            )
            _collection(database, "b2b_organizations").create_index(
                [("organization_id", ASCENDING)],
                unique=True,
                name="b2b_organization_id",
            )
            memberships = _collection(database, "b2b_memberships")
            memberships.create_index(
                [("membership_id", ASCENDING)], unique=True, name="b2b_membership_id"
            )
            memberships.create_index(
                [("user_id", ASCENDING), ("organization_id", ASCENDING)],
                unique=True,
                partialFilterExpression={"status": "active"},
                name="b2b_active_membership",
            )
    except PyMongoError:
        raise IdentityRepositoryError("B2B identity store unavailable") from None


def _date(document, key):
    value = document[key]
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError("Invalid stored datetime")
    return value.astimezone(UTC)


def _text(document, key):
    value = document[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid stored identifier")
    return value


def _user(document):
    identity = document["external_identity"]
    return User(
        user_id=_text(document, "user_id"),
        external_identity=ExternalIdentity(
            issuer=_text(identity, "issuer"), subject=_text(identity, "subject")
        ),
        status=IdentityStatus(document["status"]),
        created_at=_date(document, "created_at"),
        updated_at=_date(document, "updated_at"),
    )


def _organization(document):
    return Organization(
        organization_id=_text(document, "organization_id"),
        name=_text(document, "name"),
        status=OrganizationStatus(document["status"]),
        created_at=_date(document, "created_at"),
        updated_at=_date(document, "updated_at"),
    )


def _membership(document):
    return Membership(
        membership_id=_text(document, "membership_id"),
        user_id=_text(document, "user_id"),
        organization_id=_text(document, "organization_id"),
        role=Role(document["role"]),
        status=MembershipStatus(document["status"]),
        created_at=_date(document, "created_at"),
        updated_at=_date(document, "updated_at"),
    )


class MongoIdentityRepository:
    def __init__(self, database):
        self._collection = _collection(database, "b2b_users")

    def find_by_external_identity(self, identity: ExternalIdentity) -> tuple[User, ...]:
        try:
            with timeout(5):
                documents = list(
                    self._collection.find(
                        {
                            "external_identity.issuer": identity.issuer,
                            "external_identity.subject": identity.subject,
                        }
                    ).limit(2)
                )
                if len(documents) > 1:
                    raise ValueError("Duplicate stored external identity")
                return tuple(_user(document) for document in documents)
        except (PyMongoError, KeyError, TypeError, ValueError):
            raise IdentityRepositoryError("B2B identity store unavailable") from None


class MongoOrganizationRepository:
    def __init__(self, database):
        self._collection = _collection(database, "b2b_organizations")

    def get(self, organization_id: str) -> Organization | None:
        try:
            with timeout(5):
                documents = list(
                    self._collection.find({"organization_id": organization_id}).limit(2)
                )
                if len(documents) > 1:
                    raise ValueError("Duplicate stored organization")
                return _organization(documents[0]) if documents else None
        except (PyMongoError, KeyError, TypeError, ValueError):
            raise IdentityRepositoryError("B2B identity store unavailable") from None


class MongoMembershipRepository:
    def __init__(self, database):
        self._collection = _collection(database, "b2b_memberships")

    def find_for_user(self, user_id: str) -> tuple[Membership, ...]:
        try:
            with timeout(5):
                memberships = tuple(
                    _membership(document)
                    for document in self._collection.find({"user_id": user_id})
                )
                identifiers = [membership.membership_id for membership in memberships]
                active_organizations = [
                    membership.organization_id
                    for membership in memberships
                    if membership.status == MembershipStatus.ACTIVE
                ]
                if len(set(identifiers)) != len(identifiers) or len(
                    set(active_organizations)
                ) != len(active_organizations):
                    raise ValueError("Duplicate stored membership")
                return memberships
        except (PyMongoError, KeyError, TypeError, ValueError):
            raise IdentityRepositoryError("B2B identity store unavailable") from None
