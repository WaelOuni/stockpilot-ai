from contextlib import nullcontext
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from stockpilot.presentation.api.admin_auth import password_verifier


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("target", ["stockpilot", "stockpilot_e2e", "postgres"])
def test_postgres_provisioning_refuses_other_database_before_connection(target, monkeypatch):
    module = load_script("prepare_demo_postgres")
    monkeypatch.setattr(module.psycopg, "connect", lambda *a, **k: pytest.fail("Must not connect"))
    with pytest.raises(ValueError, match="exactly stockpilot_demo"):
        module.provision(f"postgresql://localhost/{target}")


def test_noninteractive_demo_uses_private_verifier_without_password_prompt(monkeypatch):
    module = load_script("run_demo")
    verifier = password_verifier("ephemeral-demo-only-password")
    monkeypatch.setenv("STOCKPILOT_DEMO_NONINTERACTIVE", "true")
    monkeypatch.setenv("ADMIN_USERNAME", "demo-test-admin")
    monkeypatch.setenv("ADMIN_PASSWORD_VERIFIER", verifier)
    monkeypatch.setattr(module, "getpass", lambda *a: pytest.fail("Must not prompt"))
    assert module.admin_environment() == {
        "ADMIN_USERNAME": "demo-test-admin", "ADMIN_PASSWORD_VERIFIER": verifier,
    }


@pytest.mark.parametrize("verifier", ["", "plaintext", "pbkdf2_sha256$600000$invalid"])
def test_noninteractive_demo_refuses_invalid_verifier(monkeypatch, verifier):
    module = load_script("run_demo")
    monkeypatch.setenv("STOCKPILOT_DEMO_NONINTERACTIVE", "true")
    monkeypatch.setenv("ADMIN_USERNAME", "demo-test-admin")
    monkeypatch.setenv("ADMIN_PASSWORD_VERIFIER", verifier)
    with pytest.raises(ValueError):
        module.admin_environment()


@pytest.fixture
def launcher(monkeypatch):
    for name in (
        "STOCKPILOT_DEMO_MONGO_URI", "STOCKPILOT_DEMO_POSTGRES_URL",
        "STOCKPILOT_DEMO_CHECKPOINT_URI",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MONGO_URI", "mongodb://localhost/?replicaSet=rs0")
    module = load_script("run_demo")
    monkeypatch.setattr(module, "managed_demo", lambda settings: nullcontext(module.preflight_demo(settings)))
    monkeypatch.setattr("scripts.prepare_demo_postgres.demo_uri", lambda: "postgresql://localhost/stockpilot_demo")
    return module


def test_launcher_selects_demo_before_preflight_without_changing_dev_environment(launcher, monkeypatch):
    monkeypatch.setenv("MONGO_DATABASE", "stockpilot")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/stockpilot")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DB_URI", "postgresql://localhost/stockpilot_e2e")
    environment = os.environ.copy()
    checked = []

    def preflight(settings):
        checked.append(settings)
        assert settings.mongo_database == "stockpilot_demo"
        assert settings.postgres_url == "postgresql://localhost/stockpilot_demo"
        assert settings.langgraph_checkpoint_db_uri is None
        return SimpleNamespace(mongo_summary={"reference": {
            "sha256": "test-hash", "products": 500, "orders": 2000, "stock_movements": 3000,
        }}, rag_document_count=0)

    monkeypatch.setattr(launcher, "preflight_demo", preflight)
    monkeypatch.setattr(launcher.signal, "signal", lambda *args: None)

    def serve(settings, checkpoint):
        assert settings is checked[0]
        assert checkpoint == settings.postgres_url
        return 0

    monkeypatch.setattr(launcher, "serve", serve)
    assert launcher.main() == 0
    assert os.environ == environment


def test_explicit_demo_connections_are_used_without_docker_resolution(launcher, monkeypatch):
    mongo = "mongodb://localhost/stockpilot_demo?replicaSet=rs0"
    postgres = "postgresql://localhost:5433/stockpilot_demo"
    checkpoint = "postgresql://localhost:5434/stockpilot_demo"
    monkeypatch.setenv("STOCKPILOT_DEMO_MONGO_URI", mongo)
    monkeypatch.setenv("STOCKPILOT_DEMO_POSTGRES_URL", postgres)
    monkeypatch.setenv("STOCKPILOT_DEMO_CHECKPOINT_URI", checkpoint)
    monkeypatch.setattr("scripts.prepare_demo_postgres.demo_uri", lambda: pytest.fail("Must not resolve Docker"))
    settings = launcher.demo_settings()
    assert (settings.mongo_uri, settings.postgres_url, settings.langgraph_checkpoint_db_uri) == (
        mongo, postgres, checkpoint,
    )


@pytest.mark.parametrize("target", ["stockpilot", "stockpilot_e2e"])
@pytest.mark.parametrize("name", [
    "STOCKPILOT_DEMO_MONGO_URI", "STOCKPILOT_DEMO_POSTGRES_URL",
    "STOCKPILOT_DEMO_CHECKPOINT_URI", "MONGO_URI",
])
def test_wrong_embedded_demo_target_is_refused_before_resolution(
    launcher, monkeypatch, target, name,
):
    uri = (f"mongodb://localhost/{target}?replicaSet=rs0"
           if "MONGO" in name else f"postgresql://localhost/{target}")
    monkeypatch.setenv(name, uri)
    monkeypatch.setattr("scripts.prepare_demo_postgres.demo_uri", lambda: pytest.fail("Must not resolve Docker"))
    with pytest.raises(ValueError):
        launcher.demo_settings()


@pytest.mark.parametrize("name", [
    "STOCKPILOT_DEMO_MONGO_URI", "STOCKPILOT_DEMO_POSTGRES_URL",
    "STOCKPILOT_DEMO_CHECKPOINT_URI",
])
def test_empty_demo_override_is_not_silently_replaced(launcher, monkeypatch, name):
    monkeypatch.setenv(name, "")
    monkeypatch.setattr("scripts.prepare_demo_postgres.demo_uri", lambda: pytest.fail("Must not resolve Docker"))
    with pytest.raises(ValueError, match="must not be empty"):
        launcher.demo_settings()


def test_children_receive_checked_targets_and_frontend_has_no_server_config(launcher, monkeypatch):
    mongo = "mongodb://localhost/stockpilot_demo?replicaSet=rs0"
    monkeypatch.setenv("STOCKPILOT_DEMO_MONGO_URI", mongo)
    monkeypatch.setenv("MONGO_DATABASE", "stockpilot")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://localhost/stockpilot")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DB_URI", "postgresql://localhost/stockpilot_e2e")
    monkeypatch.setenv("CUSTOMER_JWT_SECRET", "private-test-only-secret")
    settings = launcher.demo_settings()
    environments = []

    def start(*args, **kwargs):
        environments.append(kwargs["env"])
        return SimpleNamespace(poll=lambda: 0)

    monkeypatch.setattr(launcher.shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.setattr(launcher.Path, "exists", lambda self: True)
    monkeypatch.setattr(launcher, "ensure_port_is_free", lambda port: None)
    monkeypatch.setattr(launcher, "admin_environment", lambda: {})
    monkeypatch.setattr(launcher.subprocess, "Popen", start)
    monkeypatch.setattr(launcher, "_wait_for_url", lambda *args: None)
    monkeypatch.setattr(launcher, "_stop", lambda process: None)
    with pytest.raises(RuntimeError, match="demo process exited"):
        launcher.serve(settings, settings.postgres_url)
    backend, frontend = environments
    assert backend["MONGO_URI"] == settings.mongo_uri == mongo
    assert backend["MONGO_DATABASE"] == settings.mongo_database == "stockpilot_demo"
    assert backend["POSTGRES_URL"] == backend["LANGGRAPH_CHECKPOINT_DB_URI"] == settings.postgres_url
    assert backend["CUSTOMER_JWT_SECRET"] == "private-test-only-secret"
    assert not any(
        key.startswith(("MONGO", "POSTGRES", "LANGGRAPH", "CUSTOMER", "ADMIN", "VITE_"))
        for key in frontend
    )


def test_docker_resolution_failure_is_masked_and_starts_no_server(launcher, monkeypatch, capsys):
    def fail():
        raise launcher.subprocess.CalledProcessError(
            1, ["docker", "inspect"], stderr="private-test-only-secret",
        )

    monkeypatch.setattr("scripts.prepare_demo_postgres.demo_uri", fail)
    monkeypatch.setattr(launcher.signal, "signal", lambda *args: None)
    monkeypatch.setattr(launcher, "serve", lambda *args: pytest.fail("Must not start"))
    assert launcher.main() == 1
    assert "private-test-only-secret" not in capsys.readouterr().err


def test_runtime_business_uses_csrf_returned_by_login_before_product_creation(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    module = load_script("verify_demo_runtime")
    manifests = []
    database = SimpleNamespace(demo_manifests=SimpleNamespace(insert_one=manifests.append))
    dataset = SimpleNamespace(dataset_id="test-dataset", manifest={"sha256": "test-hash"})

    class StopBeforeProductCreation(Exception):
        pass

    def call(client, method, path, **kwargs):
        if path == "/api/backoffice/session/login":
            return {"csrf_token": "session-bound-test-csrf"}
        assert method == "POST" and path == "/api/backoffice/products"
        assert kwargs["headers"] == {
            "X-Admin-CSRF": "session-bound-test-csrf", "Origin": module.ORIGIN,
        }
        raise StopBeforeProductCreation

    monkeypatch.setattr(module, "call", call)
    # No real client/database: stop before the first business mutation.
    monkeypatch.setattr(database, "products", SimpleNamespace(find=lambda *args: []), raising=False)
    monkeypatch.setattr(database, "orders", SimpleNamespace(find=lambda *args: []), raising=False)
    database.demo_manifests.find_one = lambda *args: manifests[0]
    database.demo_manifests.update_one = lambda *args: None
    with pytest.raises(StopBeforeProductCreation):
        module.business(None, database, dataset, "test-admin", "test-password", "test-secret")
    assert len(manifests) == 1


@pytest.mark.parametrize("private", [False, True])
def test_launcher_and_verifiers_share_resolution_and_pinned_child_connections(launcher, monkeypatch, private):
    from stockpilot.demo_runtime import demo_connection_environment, resolve_demo_settings

    monkeypatch.setenv("MONGO_DATABASE", "stockpilot")
    monkeypatch.setenv("POSTGRES_URL", "postgresql://dev/stockpilot")
    monkeypatch.setenv("LANGGRAPH_CHECKPOINT_DB_URI", "postgresql://e2e/stockpilot_e2e")
    if private:
        monkeypatch.setenv("STOCKPILOT_DEMO_MONGO_URI", "mongodb://alias/stockpilot_demo?replicaSet=rs0")
        monkeypatch.setenv("STOCKPILOT_DEMO_POSTGRES_URL", "postgresql://alias/stockpilot_demo")
        monkeypatch.setenv("STOCKPILOT_DEMO_CHECKPOINT_URI", "postgresql://checkpoint-alias/stockpilot_demo")
    before = os.environ.copy()
    settings = launcher.demo_settings()
    runtime = load_script("verify_demo_runtime")
    restart = load_script("verify_demo_restart")
    assert launcher.demo_settings is runtime.resolve_demo_settings is restart.resolve_demo_settings is resolve_demo_settings
    assert runtime.demo_connection_environment is restart.demo_connection_environment is demo_connection_environment
    pinned = demo_connection_environment(settings)
    assert "MONGO_URI" not in pinned  # Keep the independent reference server in children.
    assert os.environ == before
    for name, value in pinned.items():
        monkeypatch.setenv(name, value)
    # A child resolves exactly the parent's three connections, without Docker fallback.
    monkeypatch.setattr("scripts.prepare_demo_postgres.demo_uri", lambda: pytest.fail("Must use pinned URI"))
    child = resolve_demo_settings()
    assert (child.mongo_uri, child.postgres_url, child.langgraph_checkpoint_db_uri) == (
        settings.mongo_uri, settings.postgres_url,
        settings.langgraph_checkpoint_db_uri or settings.postgres_url,
    )


def test_provisioning_refuses_unexpected_server_before_any_write(monkeypatch):
    module = load_script("prepare_demo_postgres")
    monkeypatch.setattr(module, "existing_server_uri", lambda: "postgresql://local/postgres")
    monkeypatch.setattr(module, "postgres_server_identity", lambda uri: "other" if "unexpected" in uri else "local")
    monkeypatch.setattr(module.psycopg, "connect", lambda *args, **kwargs: pytest.fail("Must not write"))
    with pytest.raises(ValueError, match="Unexpected PostgreSQL provisioning server"):
        module.provision("postgresql://unexpected/stockpilot_demo")
