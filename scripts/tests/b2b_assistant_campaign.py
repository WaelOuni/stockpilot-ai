"""Synthetic tenant fixtures and actual Ollama/Chromium in the owned BFF harness."""

import asyncio
import base64
import json
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import psycopg
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from stockpilot.application.services.identity_authorization import (
    IdentityAuthorizationService,
)
from stockpilot.application.services.tenant_authorization import (
    TenantAuthorizationService,
)
from stockpilot.domain.entities.identity import ExternalIdentity
from stockpilot.domain.entities.tenant_authorization import TenantContext
from stockpilot.domain.entities.tenant_knowledge import IngestTenantDocument
from stockpilot.infrastructure.embeddings.ollama_embedding_adapter import (
    OllamaEmbeddingAdapter,
)
from stockpilot.infrastructure.persistence.mongodb.b2b_identity import (
    MongoIdentityRepository,
    MongoMembershipRepository,
    MongoOrganizationRepository,
)
from stockpilot.infrastructure.persistence.postgres.tenant_knowledge import (
    PgTenantKnowledge,
)


def run_assistant_browser(db, env, directory, cert, passed, report):
    assert env["B2B_BROWSER_TEST"] == "isolated" and db.name.startswith(
        "stockpilot_b2b_test_"
    )
    # Direct fixture provisioning only, never a callback or public route.
    saved_roles = {}
    for user in ("fixture-user", "fixture-other-user"):
        saved_roles[user] = db.b2b_memberships.find_one({"user_id": user})["role"]
        db.b2b_memberships.update_one(
            {"user_id": user}, {"$set": {"role": "ORGANIZATION_ADMIN"}}
        )
    db.b2b_products.update_one(
        {"organization_id": "fixture-org", "sku": "DRILL-001"},
        {
            "$set": {
                "quantity": 10,
                "reserved": 0,
                "price_minor": 4900,
                "name": "Perceuse Northline 18V 0476",
            },
            "$inc": {"revision": 1},
        },
    )
    db.b2b_products.update_one(
        {"organization_id": "fixture-other", "sku": "DRILL-001"},
        {
            "$set": {
                "quantity": 2,
                "reserved": 0,
                "price_minor": 9900,
                "name": "Perceuse B",
            },
            "$inc": {"revision": 1},
        },
    )

    class Authentication:
        def authenticate(self, user):
            row = db.b2b_users.find_one({"user_id": user})
            return ExternalIdentity(**row["external_identity"]) if row else None

    resolver = IdentityAuthorizationService(
        Authentication(),
        MongoIdentityRepository(db),
        MongoOrganizationRepository(db),
        MongoMembershipRepository(db),
    )

    class Source:
        def current(self, user):
            org = "fixture-org" if user == "fixture-user" else "fixture-other"
            return TenantContext(resolver.resolve(user, org), "fixture-provisioning")

    authorization = TenantAuthorizationService(Source(), None, None)
    settings = SimpleNamespace(
        ollama_base_url=env["BFF_ASSISTANT_OLLAMA_URL"],
        ollama_embedding_model="nomic-embed-text",
        ollama_timeout_seconds=60,
        ollama_max_prompt_bytes=24576,
    )
    embeddings = OllamaEmbeddingAdapter(settings)
    repo = PgTenantKnowledge(
        env["BFF_ASSISTANT_POSTGRES_URI"],
        authorization,
        embeddings,
        model="nomic-embed-text",
        version="v1",
    )
    try:
        for user, content in (
            (
                "fixture-user",
                "Le code documentaire de garantie est ALPHA-ONLY. La garantie synthétique dure 24 mois.",
            ),
            (
                "fixture-other-user",
                "Le code documentaire de garantie est BETA-ONLY. La garantie synthétique dure 6 mois.",
            ),
        ):
            started = perf_counter()
            asyncio.run(
                repo.ingest(
                    user,
                    Source().current(user),
                    IngestTenantDocument(
                        "seed-document",
                        "same-document",
                        "Garantie",
                        content,
                        "nomic-embed-text",
                        "v1",
                        0,
                    ),
                )
            )
            report.setdefault("embedding_fixture_ms", []).append(
                round((perf_counter() - started) * 1000, 2)
            )
    finally:
        for user, role in saved_roles.items():
            db.b2b_memberships.update_one({"user_id": user}, {"$set": {"role": role}})
    certificate = x509.load_pem_x509_certificate(cert.read_bytes())
    spki = certificate.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    browser_env = env | {
        "NODE_EXTRA_CA_CERTS": str(cert),
        "B2B_BROWSER_SPKI": base64.b64encode(sha256(spki).digest()).decode(),
        "B2B_BROWSER_DIRECTORY": str(directory),
        "B2B_BROWSER_REPORT": str(directory / "assistant-browser-report.json"),
    }
    with (directory / "assistant-browser-private.log").open("w") as output:
        result = subprocess.run(
            [shutil.which("node"), "b2b-assistant-browser.mjs"],
            cwd=Path(__file__).resolve().parents[2] / "frontend",
            env=browser_env,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=False,
        )
    browser_report = json.loads(
        (directory / "assistant-browser-report.json").read_text()
    )
    with psycopg.connect(env["BFF_ASSISTANT_POSTGRES_URI"]) as c:
        rows = c.execute(
            "SELECT reply FROM stockpilot_b2b.assistant_turns WHERE kind='message' AND status='completed' ORDER BY created_at"
        ).fetchall()
    report["assistant_timings"] = [
        {
            "intent": row[0].get("intent"),
            "timing_ms": row[0].get("timing_ms"),
            "rag_timing_ms": row[0].get("rag_timing_ms"),
        }
        for row in rows
    ]
    report["assistant_browser"] = browser_report
    if result.returncode or not browser_report["passed"]:
        raise RuntimeError(
            "Real assistant browser campaign failed; inspect private diagnostics."
        )
    for item in browser_report["checks"]:
        passed("Assistant Chromium/Ollama: " + item)
    # Verify committed state independently of HTTP/UI.
    assert (
        db.b2b_products.find_one(
            {"organization_id": "fixture-org", "sku": "DRILL-001"}
        )["reserved"]
        == 0
    )
    assert (
        db.b2b_products.find_one(
            {"organization_id": "fixture-other", "sku": "DRILL-001"}
        )["reserved"]
        == 0
    )
    order = db.b2b_orders.find_one({"order_id": browser_report["order_id"]})
    assert order["status"] == "cancelled" and order["created_by"] == "fixture-user"
    assert (
        db.b2b_order_operations.count_documents(
            {"order_id": browser_report["order_id"], "namespace": "confirm"}
        )
        == 1
    )
    with psycopg.connect(env["BFF_ASSISTANT_POSTGRES_URI"]) as c:
        rows = c.execute(
            "SELECT reply FROM stockpilot_b2b.assistant_turns WHERE kind='message' AND status='completed' ORDER BY created_at"
        ).fetchall()
    report["assistant_timings"] = [
        {
            "intent": row[0].get("intent"),
            "timing_ms": row[0].get("timing_ms"),
            "rag_timing_ms": row[0].get("rag_timing_ms"),
        }
        for row in rows
    ]
    report["assistant_browser"] = browser_report
