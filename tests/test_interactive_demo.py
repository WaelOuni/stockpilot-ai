import json

"""Non-mutating operator contracts; real lifecycle is validated separately."""

from unittest.mock import Mock

import pytest

from scripts import b2b_interactive as demo


@pytest.fixture
def owner(tmp_path):
    state = tmp_path / "stockpilot-v139-test-unit"
    state.mkdir(mode=0o700)
    o = demo.InteractiveDemo(state, ephemeral=True)
    installation = "a" * 32
    o.manifest = {
        "version": 1,
        "installation": installation,
        "project": demo.PROJECT + "-test-" + installation[:12],
        "purpose": demo.PURPOSE,
        "state": str(state),
        "phase": "ready",
        "template_sha256": demo.digest(demo.TEMPLATE),
        "containers": {},
        "images": {},
        "networks": {},
        "volumes": {},
    }
    demo.certificates(state)
    for name in ["bff.json", "operator.json", "realm.json", "mongo.key", "proxy.conf"]:
        (state / name).write_text("{}")
        (state / name).chmod(0o640)
    for name in ["credentials.json", "compose.json"]:
        demo.write_json(state / name, {})
    o.seal()
    return o


def test_real_generated_file_permissions_load_and_certificates_stable(owner):
    before = {p.name: p.read_bytes() for p in owner.state.iterdir()}
    owner.load()
    owner.load()
    assert {p.name: p.read_bytes() for p in owner.state.iterdir()} == before


@pytest.mark.parametrize(
    "name",
    [
        "bff.json",
        "operator.json",
        "tls.crt",
        "tls.key",
        "ca.crt",
        "ca.key",
        "realm.json",
        "mongo.key",
        "proxy.conf",
        "credentials.json",
        "compose.json",
    ],
)
def test_missing_file_refuses_before_docker(owner, monkeypatch, name):
    (owner.state / name).unlink()
    command = Mock()
    monkeypatch.setattr(demo, "command", command)
    with pytest.raises(demo.DemoError):
        owner.load()
    command.assert_not_called()


@pytest.mark.parametrize(
    "name",
    [
        "bff.json",
        "operator.json",
        "credentials.json",
        "tls.key",
        "ca.key",
        "proxy.conf",
    ],
)
@pytest.mark.parametrize("mode", [0o644, 0o666, 0o777])
def test_insecure_permissions_refused(owner, name, mode):
    (owner.state / name).chmod(mode)
    with pytest.raises(demo.DemoError):
        owner.load()


def test_changed_secret_refused(owner):
    (owner.state / "credentials.json").write_text('{"changed":true}')
    with pytest.raises(demo.DemoError, match="changed"):
        owner.load()


def test_symlink_secret_refused(owner):
    path = owner.state / "tls.key"
    path.unlink()
    path.symlink_to(owner.state / "ca.key")
    with pytest.raises(demo.DemoError):
        owner.load()


@pytest.mark.parametrize(
    "field,value",
    [
        ("phase", "initializing"),
        ("project", "stockpilot-v133-candidate"),
        ("purpose", "historical"),
        ("state", "/home/waelko/projects/stockpilot-ai"),
        ("version", 999),
    ],
)
def test_foreign_manifest_refused(owner, field, value):
    owner.manifest[field] = value
    owner.save()
    with pytest.raises(demo.DemoError):
        owner.load()


def test_one_public_installation_path_only(tmp_path):
    with pytest.raises(demo.DemoError):
        demo.InteractiveDemo(tmp_path / "second-demo")


def test_ephemeral_seam_requires_clear_test_name(tmp_path):
    with pytest.raises(demo.DemoError):
        demo.InteractiveDemo(tmp_path / "historical", ephemeral=True)


def test_partial_init_never_reseeds(owner, monkeypatch):
    owner.manifest["phase"] = "initializing"
    owner.save()
    command = Mock()
    monkeypatch.setattr(demo, "command", command)
    with pytest.raises(demo.DemoError):
        owner.init("", "")
    command.assert_not_called()


def test_repeat_init_is_read_only(owner, monkeypatch):
    monkeypatch.setattr(owner, "attest", Mock(return_value={}))
    assert owner.init("", "")["unchanged"]
    assert owner.manifest["phase"] == "ready"


def test_lock_rejects_concurrent_lifecycle(owner):
    with owner.locked(), pytest.raises(demo.DemoError, match="active"), owner.locked():
        pass


def test_low_disk_gate(monkeypatch):
    monkeypatch.setattr(demo, "capacity", lambda: {"linux": 1, "windows": 10**12})
    with pytest.raises(demo.DemoError, match="10 GiB"):
        demo.require_capacity()


def test_occupied_port_refused_without_docker(monkeypatch):
    sock = Mock()
    sock.__enter__ = Mock(side_effect=OSError)
    sock.__exit__ = Mock()
    monkeypatch.setattr(demo.socket, "socket", lambda: sock)
    with pytest.raises(demo.DemoError, match="occupied"):
        demo.check_ports()


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1:9/secret",
        "http://user:password@127.0.0.1",
        "",
        "file:///tmp/private",
    ],
)
def test_non_private_or_invalid_ollama_refused(url):
    assert not demo.ollama_status(url)["available"]


def test_command_redacts_errors(monkeypatch):
    monkeypatch.setattr(
        demo.subprocess,
        "run",
        lambda *a, **k: type(
            "P", (), {"returncode": 1, "stdout": "secret", "stderr": "password"}
        )(),
    )
    with pytest.raises(demo.DemoError) as error:
        demo.command(["fake", "secret"])
    assert "secret" not in str(error.value) and "password" not in str(error.value)


def test_stop_is_term_only_and_does_not_remove(owner, monkeypatch):
    owner.manifest["containers"] = {n: n for n in demo.SERVICES}
    owner.seal()
    rows = {n: {"Id": n, "State": {"Running": True}} for n in demo.SERVICES}
    monkeypatch.setattr(owner, "attest", lambda: rows)
    monkeypatch.setattr(owner, "worker", lambda action: {"pending_assistant_turns": 0})
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return '{"Running":false,"ExitCode":0}' if "inspect" in args else ""

    monkeypatch.setattr(demo, "command", run)
    result = owner.stop()
    assert not result["running"]
    signals = [a for a in calls if "kill" in a]
    assert all("--signal=SIGTERM" in a for a in signals)
    assert [a[2] for a in calls if a[:2] == ["docker", "exec"]] == ["proxy"]
    assert not any(
        a
        for a in calls
        if any(x in a for x in ["rm", "down", "--volumes", "-v", "--force", "SIGKILL"])
    )


def test_start_windows_gate_precedes_mutations(owner, monkeypatch):
    monkeypatch.setattr(owner, "attest", dict)
    monkeypatch.setattr(demo, "require_capacity", lambda: None)
    monkeypatch.setattr(
        demo, "windows_preflight", lambda p: {"dns": False, "ca_trusted": False}
    )
    compose = Mock()
    monkeypatch.setattr(owner, "compose", compose)
    with pytest.raises(demo.DemoError, match="Windows"):
        owner.start()
    compose.assert_not_called()


def test_stop_already_stopped_preserves_every_file(owner, monkeypatch):
    monkeypatch.setattr(
        owner,
        "attest",
        lambda: {n: {"Id": n, "State": {"Running": False}} for n in demo.SERVICES},
    )
    run = Mock()
    monkeypatch.setattr(demo, "command", run)
    before = {p.name: p.read_bytes() for p in owner.state.iterdir()}
    owner.stop()
    run.assert_not_called()
    assert {p.name: p.read_bytes() for p in owner.state.iterdir()} == before


def test_template_still_has_operator_profile_and_no_historical_changes():
    import yaml

    data = yaml.safe_load(demo.TEMPLATE.read_text())
    assert data["services"]["operator"]["profiles"] == ["operator"]
    assert data["services"]["operator"]["read_only"]
    assert data["services"]["mongo"]["ports"] == [
        "127.0.0.1:${SP_DEMO_MONGO_PORT:-57916}:27017"
    ]


@pytest.mark.parametrize(
    "kind",
    [
        "project-network",
        "edge",
        "application",
        "identity",
        "egress",
        "mongo-data",
        "mongo-config",
        "postgres-data",
        "keycloak-data",
    ],
)
def test_init_refuses_preexisting_network_or_volume_without_adopting(
    tmp_path, monkeypatch, kind
):
    state = tmp_path / "stockpilot-v139-test-existing"
    owner = demo.InteractiveDemo(state, ephemeral=True)
    monkeypatch.setattr(owner, "docker", lambda: None)
    monkeypatch.setattr(demo, "require_capacity", lambda: None)
    monkeypatch.setattr(demo, "check_ports", lambda: None)
    monkeypatch.setattr(demo, "ollama_status", lambda url: {"available": True})

    def run(args, **kwargs):
        if (
            kind == "project-network"
            and args[:3] == ["docker", "network", "ls"]
            and any("label=" in a for a in args)
        ):
            return "foreign-network"
        if any(a.endswith("_" + kind + "$") for a in args):
            return "foreign-resource"
        return ""

    monkeypatch.setattr(demo, "command", run)
    with pytest.raises(demo.DemoError, match="exist"):
        owner.init("http://127.0.0.1:11434", "http://host.docker.internal:11434")
    assert not state.exists()


def test_windows_trust_probe_does_not_install_certificates_or_modify_hosts():
    import inspect

    source = inspect.getsource(demo.windows_preflight)
    assert "GetHostAddresses" in source
    assert "Import-Certificate" not in source and "Set-Content" not in source


def test_invalid_root_without_manifest_never_initializes_over_it(tmp_path):
    state = tmp_path / "stockpilot-v139-test-occupied"
    state.mkdir(mode=0o700)
    sentinel = state / "data"
    sentinel.write_text("keep")
    with pytest.raises(demo.DemoError):
        demo.InteractiveDemo(state, ephemeral=True).init("", "")
    assert sentinel.read_text() == "keep"


def test_generated_replica_key_uses_mongodb_base64_alphabet():
    import inspect

    source = inspect.getsource(demo.InteractiveDemo.init)
    assert "base64.b64encode" in source


def test_interactive_render_has_no_database_host_ports_or_key_bind(tmp_path):
    proxy = tmp_path / "proxy.conf"
    proxy.write_text("private")
    config = {
        "networks": {"edge": {}, "identity": {}},
        "services": {
            "auth-mongo": {},
            "mongo": {
                "ports": ["127.0.0.1:57916:27017"],
                "volumes": [],
                "command": ["mongod"],
                "healthcheck": {},
            },
            "test-postgres": {"ports": ["127.0.0.1:57918:55432"]},
            "keycloak-db": {"environment": {}},
            "keycloak": {
                "environment": {
                    "BFF_TEST_USER_PASSWORD": "synthetic",
                    "BFF_TEST_CLIENT_SECRET": "synthetic",
                }
            },
            "bff": {
                "volumes": [
                    {
                        "source": str(tmp_path / "tls.crt"),
                        "target": "/run/stockpilot/ca.crt",
                    }
                ]
            },
            "proxy": {
                "volumes": [
                    {"source": str(proxy), "target": "/etc/nginx/conf.d/default.conf"}
                ]
            },
        },
    }
    rendered = demo.interactive_composition(
        config,
        {
            "mongo_password": "synthetic",
            "keycloak_db_password": "synthetic",
            "replica_key": "synthetic-base64",
        },
        "a" * 32,
    )
    assert "auth-mongo" not in rendered["services"]
    assert "ports" not in rendered["services"]["mongo"]
    assert "ports" not in rendered["services"]["test-postgres"]
    assert rendered["services"]["mongo"]["volumes"] == []
    assert "/data/configdb/replica.key" in rendered["services"]["mongo"]["command"]
    assert rendered["services"]["bff"]["volumes"][0]["source"] == str(
        tmp_path / "ca.crt"
    )
    assert (
        "BFF_TEST_USER_PASSWORD" not in rendered["services"]["keycloak"]["environment"]
    )


def test_no_provisioning_during_resume():
    import inspect

    source = inspect.getsource(demo.InteractiveDemo.start)
    assert 'worker("check")' in source
    assert 'worker("init")' not in source
    assert " up " not in source
    assert "wait_services" in source


@pytest.mark.parametrize(
    "failure",
    [
        "missing-volume",
        "replaced-volume",
        "foreign-endpoint",
        "foreign-container",
        "missing-container",
    ],
)
def test_attest_refuses_resource_drift_before_any_mutation(owner, monkeypatch, failure):
    import json

    ids = {name: "id-" + name for name in demo.SERVICES}
    owner.manifest.update(
        containers=ids, images={name: "image-" + name for name in demo.SERVICES}
    )
    record = {
        "Name": "only-owned-volume",
        "CreatedAt": "original",
        "Driver": "local",
        "Labels": {},
        "Options": None,
        "Scope": "local",
    }
    owner.manifest["volumes"] = {record["Name"]: demo.volume_identity(record)}
    owner.manifest["networks"] = {"only-owned-network": "network-id"}
    rows = [
        {
            "Id": ids[name],
            "Image": "image-" + name,
            "Config": {
                "Labels": {
                    "com.docker.compose.service": name,
                    "stockpilot.audit.purpose": demo.PURPOSE,
                    "com.docker.compose.project.config_files": str(
                        owner.state / "compose.json"
                    ),
                }
            },
            "Mounts": [],
            "HostConfig": {"PortBindings": {}},
        }
        for name in demo.SERVICES
    ]
    if failure == "foreign-container":
        rows[0]["Config"]["Labels"]["stockpilot.audit.purpose"] = "historical"
    if failure == "missing-container":
        rows.pop()
    owner.save()
    owner.load()
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:3] == ["docker", "ps", "-aq"]:
            return " ".join(ids.values())
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(rows)
        if args[:3] == ["docker", "volume", "inspect"]:
            if failure == "missing-volume":
                raise demo.DemoError("Volume absent.")
            return json.dumps(
                [
                    record
                    | (
                        {"CreatedAt": "replacement"}
                        if failure == "replaced-volume"
                        else {}
                    )
                ]
            )
        if args[:3] == ["docker", "network", "inspect"]:
            return json.dumps(
                [
                    {
                        "Id": "network-id",
                        "Labels": {
                            "stockpilot.audit.purpose": demo.PURPOSE,
                            "com.docker.compose.project": owner.manifest["project"],
                        },
                        "Containers": {"foreign-id": {}}
                        if failure == "foreign-endpoint"
                        else {},
                    }
                ]
            )
        raise AssertionError("Unexpected command")

    monkeypatch.setattr(owner, "docker", lambda: None)
    monkeypatch.setattr(demo, "command", run)
    with pytest.raises(demo.DemoError):
        owner.attest()
    assert all(
        "start" not in args and "up" not in args and "rm" not in args for args in calls
    )


def test_failed_bff_drain_never_stops_databases_or_forces_exit(owner, monkeypatch):
    rows = {n: {"Id": n, "State": {"Running": True}} for n in demo.SERVICES}
    monkeypatch.setattr(owner, "attest", lambda: rows)
    times = iter([0, 0, 0, 0, 200])
    monkeypatch.setattr(demo.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(demo.time, "sleep", lambda seconds: None)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "inspect" in args:
            return (
                '{"Running":false,"ExitCode":0}'
                if args[-1] == "proxy"
                else '{"Running":true}'
            )
        return ""

    monkeypatch.setattr(demo, "command", run)
    with pytest.raises(demo.DemoError, match="drain"):
        owner.stop()
    signals = [args[-1] for args in calls if "--signal=SIGTERM" in args]
    assert signals == ["bff"]
    assert not any("SIGKILL" in " ".join(args) for args in calls)


def test_incomplete_schema_refuses_before_bff_start(owner, monkeypatch):
    owner.manifest["host_ollama"] = "http://127.0.0.1:11434"
    owner.save()
    monkeypatch.setattr(
        owner, "attest", lambda: {"proxy": {"State": {"Running": True}}}
    )
    monkeypatch.setattr(demo, "require_capacity", lambda: None)
    monkeypatch.setattr(demo, "ollama_status", lambda url: {"available": True})
    monkeypatch.setattr(owner, "wait_services", lambda names: None)
    monkeypatch.setattr(
        owner, "worker", Mock(side_effect=demo.DemoError("Incomplete schema."))
    )
    compose = Mock()
    monkeypatch.setattr(owner, "compose", compose)
    with pytest.raises(demo.DemoError, match="schema"):
        owner.start(require_windows=False)
    assert compose.call_args_list == [((("start",) + demo.INFRA),)]


def test_unavailable_docker_never_creates_installation(tmp_path, monkeypatch):
    state = tmp_path / "stockpilot-v139-test-unavailable"
    operator = demo.InteractiveDemo(state, ephemeral=True)
    monkeypatch.setattr(
        operator, "docker", Mock(side_effect=demo.DemoError("Docker unavailable."))
    )
    with pytest.raises(demo.DemoError):
        operator.init("", "")
    assert not state.exists()


def test_unavailable_models_never_creates_installation(tmp_path, monkeypatch):
    state = tmp_path / "stockpilot-v139-test-models"
    operator = demo.InteractiveDemo(state, ephemeral=True)
    monkeypatch.setattr(operator, "docker", lambda: None)
    monkeypatch.setattr(demo, "require_capacity", lambda: None)
    monkeypatch.setattr(demo, "check_ports", lambda: None)
    monkeypatch.setattr(demo, "ollama_status", lambda url: {"available": False})
    with pytest.raises(demo.DemoError, match="Ollama"):
        operator.init("http://127.0.0.1:11434", "http://host.docker.internal:11434")
    assert not state.exists()


@pytest.mark.parametrize("exit_code,oom", [(1, False), (137, False), (143, True)])
def test_unclean_bff_exit_retains_databases(owner, monkeypatch, exit_code, oom):
    rows = {n: {"Id": n, "State": {"Running": True}} for n in demo.SERVICES}
    monkeypatch.setattr(owner, "attest", lambda: rows)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "inspect" in args:
            return json.dumps(
                {
                    "Running": False,
                    "ExitCode": 0 if args[-1] == "proxy" else exit_code,
                    "OOMKilled": False if args[-1] == "proxy" else oom,
                }
            )
        return ""

    monkeypatch.setattr(demo, "command", run)
    with pytest.raises(demo.DemoError, match="Unclean"):
        owner.stop()
    assert [args[-1] for args in calls if "--signal=SIGTERM" in args] == ["bff"]
    assert not any("rm" in args or "down" in args for args in calls)
