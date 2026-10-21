"""Disposable HTTPS Vue/BFF composition. Never a deployment entrypoint.

Called only by validate_bff_keycloak.py --frontend after owned tmpfs checks.
No proxy to the historical API. No middleware or CSP from API is relaxed.
"""

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import FileResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from stockpilot.bff import create_app as create_bff
from stockpilot.infrastructure.config.bff_settings import BffSettings


def create_app():
    settings = BffSettings()
    if (
        os.environ.get("B2B_BROWSER_TEST") != "isolated"
        or settings.mode != "b2b_business_test"
        or not re.fullmatch(
            r"stockpilot_b2b_test_[a-f0-9]{32}", settings.mongo_database
        )
        or settings.origin != "https://127.0.0.1:57914"
    ):
        raise RuntimeError("Disposable browser configuration required.")
    settings.validated_policy()
    root = Path(__file__).resolve().parents[2] / "frontend/dist"
    if not (root / "index.html").is_file():
        raise RuntimeError("Build the isolated Vue worktree before the campaign.")
    bff = create_bff(settings)

    @asynccontextmanager
    async def lifespan(app):
        async with bff.router.lifespan_context(bff):
            yield

    async def vue(request):
        return FileResponse(
            root / "index.html",
            headers={
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'self'; connect-src 'self'; "
                    "script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
                ),
            },
        )

    return Starlette(
        lifespan=lifespan,
        routes=[
            Route("/b2b-app", vue),
            Route("/b2b-app/{path:path}", vue),
            Mount("/assets", StaticFiles(directory=root / "assets")),
            Mount("/", bff),
        ],
    )
