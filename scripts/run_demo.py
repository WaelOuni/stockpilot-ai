"""Start the integrated local UI/API only after read-only demo-store preflight."""

from getpass import getpass
import hmac
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen

from pymongo.errors import PyMongoError
import psycopg

from stockpilot.demo_runtime import (
    preflight_demo, resolve_demo_settings, demo_connection_environment,
)
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.presentation.api.admin_auth import password_verifier


ROOT = Path(__file__).resolve().parents[1]
API_PORT = 8003
WEB_PORT = 5175
API_ORIGIN = f"http://127.0.0.1:{API_PORT}"
WEB_ORIGIN = f"http://127.0.0.1:{WEB_PORT}"


demo_settings = resolve_demo_settings


def ensure_port_is_free(port: int) -> None:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            raise ValueError(f"Demo port {port} is unavailable; no existing process was replaced.") from None


def admin_environment() -> dict[str, str]:
    if os.environ.get("STOCKPILOT_DEMO_NONINTERACTIVE") == "true":
        username = os.environ.get("ADMIN_USERNAME", "")
        verifier = os.environ.get("ADMIN_PASSWORD_VERIFIER", "")
        if (not 1 <= len(username.strip()) <= 128 or not re.fullmatch(
            r"pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}", verifier,
        )):
            raise ValueError("Noninteractive demo requires an ephemeral administrator verifier.")
        return {"ADMIN_USERNAME": username, "ADMIN_PASSWORD_VERIFIER": verifier}
    username = input("Demo administrator username: ").strip()
    if not username or len(username) > 128:
        raise ValueError("Demo administrator username must contain 1..128 characters.")
    password = getpass("Demo administrator password (16..256 characters): ")
    confirmation = getpass("Repeat password: ")
    if not 16 <= len(password) <= 256 or not hmac.compare_digest(password, confirmation):
        raise ValueError("Demo administrator password is invalid or the confirmation differs.")
    settings = Settings()
    if any(
        secret is not None and hmac.compare_digest(password, secret.get_secret_value())
        for secret in (settings.backoffice_api_token, settings.customer_jwt_secret)
    ):
        raise ValueError("Demo administrator password must differ from other server secrets.")
    verifier = password_verifier(password)
    del password, confirmation
    return {
        "ADMIN_USERNAME": username,
        "ADMIN_PASSWORD_VERIFIER": verifier,
        "ADMIN_TRUSTED_ORIGIN": WEB_ORIGIN,
        "ADMIN_ALLOW_LOCAL_HTTP": "true",
        "DEPLOYMENT_ENVIRONMENT": "development",
    }


def _wait_for_url(process: subprocess.Popen, url: str, label: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"{label} exited before becoming ready.")
        try:
            with urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError):
            time.sleep(0.25)
    raise RuntimeError(f"{label} did not become ready within {timeout:g} seconds.")


def _stop(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def serve(settings: Settings, checkpoint_uri: str) -> int:
    npm = shutil.which("npm")
    if npm is None or not (ROOT / "frontend" / "node_modules" / ".bin" / "vite").exists():
        raise ValueError("Frontend dependencies are not installed; no dependency installation was attempted.")
    ensure_port_is_free(API_PORT)
    ensure_port_is_free(WEB_PORT)

    server_environment = os.environ.copy()
    server_environment.pop("DEMO_ADMIN_PASSWORD", None)
    server_environment.update({
        **demo_connection_environment(settings),
        "MONGO_URI": settings.mongo_uri,
        "ADMIN_ALLOW_LOCAL_HTTP": "true",
        "DEPLOYMENT_ENVIRONMENT": "development",
        "ADMIN_TRUSTED_ORIGIN": WEB_ORIGIN,
        "OTEL_ENABLED": "false",
        "LOG_FILE": "",
        **admin_environment(),
    })
    frontend_environment = {
        key: os.environ[key]
        for key in ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    api_process = None
    web_process = None
    with tempfile.TemporaryDirectory(prefix=".demo-env-", dir=ROOT) as isolated_env_dir:
        frontend_environment.update({
            "API_PROXY_TARGET": API_ORIGIN,
            "STOCKPILOT_DEMO_ENV_DIR": isolated_env_dir,
        })
        try:
            api_process = subprocess.Popen(
                [
                    sys.executable, "-m", "uvicorn", "stockpilot.main:app",
                    "--host", "127.0.0.1", "--port", str(API_PORT),
                ],
                cwd=ROOT,
                env=server_environment,
                start_new_session=True,
            )
            _wait_for_url(api_process, API_ORIGIN + "/health", "FastAPI")
            web_process = subprocess.Popen(
                [
                    npm, "run", "dev", "--", "--host", "127.0.0.1",
                    "--port", str(WEB_PORT), "--strictPort",
                ],
                cwd=ROOT / "frontend",
                env=frontend_environment,
                start_new_session=True,
            )
            _wait_for_url(web_process, WEB_ORIGIN, "Vue frontend")
            print(f"Demo UI: {WEB_ORIGIN}  API: {API_ORIGIN}")
            print("Ctrl+C stops only the two processes started by this launcher.")
            while api_process.poll() is None and web_process.poll() is None:
                time.sleep(0.25)
            raise RuntimeError("A demo process exited; the other process will be stopped.")
        finally:
            _stop(web_process)
            _stop(api_process)
    return 0


def main() -> int:
    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        settings = demo_settings()
        preflight = preflight_demo(settings)
        print(
            "Read-only preflight passed: immutable Mongo reference "
            f"{preflight.mongo_summary['reference']['sha256']} "
            f"({preflight.mongo_summary['reference']['products']} products, "
            f"{preflight.mongo_summary['reference']['orders']} orders, "
            f"{preflight.mongo_summary['reference']['stock_movements']} movements); "
            f"RAG rows={preflight.rag_document_count}."
        )
        checkpoint_uri = settings.langgraph_checkpoint_db_uri or settings.postgres_url
        return serve(settings, checkpoint_uri)
    except KeyboardInterrupt:
        print("Demo stopped.")
        return 130
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError,
            PyMongoError, psycopg.Error) as error:
        message = str(error) if isinstance(error, (ValueError, RuntimeError)) else (
            f"Infrastructure failure ({type(error).__name__}); connection details are withheld."
        )
        print(f"Integrated demo refused: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
