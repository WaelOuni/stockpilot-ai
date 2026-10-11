"""V8 no-seed browser check against existing stockpilot_e2e catalogue only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time
import tempfile

import httpx
import jwt
from bson.json_util import dumps, CANONICAL_JSON_OPTIONS
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import InMemorySaver
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri

from stockpilot.application.use_cases.catalogue_advice import CatalogueAdviceUseCase, is_catalogue_advice
from stockpilot.application.use_cases.catalog_products import GetProductUseCase, ListProductsUseCase
from stockpilot.application.use_cases.check_stock import CheckStockUseCase
from stockpilot.application.use_cases.classify_intent import ClassifyIntentUseCase
from stockpilot.application.workflows.stockpilot_workflow import build_graph
from stockpilot.infrastructure.config.settings import Settings
from stockpilot.infrastructure.llm.ollama_adapter import OllamaAdapter
from stockpilot.infrastructure.persistence.mongodb.product_repository import MongoProductRepository
from stockpilot.presentation.api.routes.assistant import router as assistant_router
from stockpilot.presentation.api.routes.products import router as products_router

ROOT = Path(__file__).resolve().parents[1]
DATABASE = 'stockpilot_e2e'
API = 'http://127.0.0.1:8002'
UI = 'http://127.0.0.1:5174'


def settings():
    if os.environ.get('MONGO_DATABASE') != DATABASE:
        raise ValueError('Explicit stockpilot_e2e target required.')
    uri = os.environ.get('E2E_MONGO_URI') or Settings().mongo_uri
    parsed = parse_uri(uri)
    if parsed.get('database') not in (None, DATABASE) or parsed['options'].get('replicaSet') != 'rs0':
        raise ValueError('E2E Mongo URI must target stockpilot_e2e on rs0.')
    return Settings(mongo_uri=uri, mongo_database=DATABASE, log_file=None,
                    langgraph_checkpoint_db_uri=None, otel_enabled=False)


def database(client):
    hello = client.admin.command('hello')
    if hello.get('setName') != 'rs0' or not hello.get('isWritablePrimary'):
        raise ValueError('Actual E2E Mongo primary must be rs0.')
    db = client.get_database(DATABASE)
    if db.name != DATABASE or not db.products.find_one():
        raise ValueError('Existing stockpilot_e2e catalogue required; no seed is allowed.')
    return db


def snapshot(db):
    result = {}
    for name in sorted(db.list_collection_names()):
        content = [list(db[name].find({}).sort('_id', 1)), list(db[name].list_indexes())]
        result[name] = hashlib.sha256(dumps(content, json_options=CANONICAL_JSON_OPTIONS).encode()).hexdigest()
    return result


class ForbiddenMutation:
    async def execute(self, *args, **kwargs):
        raise RuntimeError('Business mutation forbidden in advice browser harness.')


def serve():
    import uvicorn
    configured = settings()
    client = MongoClient(configured.mongo_uri, serverSelectionTimeoutMS=5000)
    repo = MongoProductRepository(database(client))
    llm = OllamaAdapter(configured)
    forbidden = ForbiddenMutation()
    app = FastAPI()
    app.state.settings = configured
    app.state.stockpilot_workflow = build_graph(
        ClassifyIntentUseCase(llm), CheckStockUseCase(repo), forbidden, forbidden, forbidden,
        get_order_status=forbidden, catalogue_advice=CatalogueAdviceUseCase(repo, llm),
        checkpointer=InMemorySaver(),
    )
    app.state.list_products_use_case = ListProductsUseCase(repo)
    app.state.get_product_use_case = GetProductUseCase(repo)

    @app.middleware('http')
    async def no_business_mutations(request: Request, next_handler):
        if request.method != 'GET':
            if request.method != 'POST' or request.url.path != '/assistant':
                return JSONResponse({'detail': 'Mutation forbidden.'}, status_code=405)
            body = await request.json()
            if not is_catalogue_advice(body.get('message', '')):
                return JSONResponse({'detail': 'Only explicit catalogue advice is allowed.'}, status_code=422)
        return await next_handler(request)

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    app.include_router(products_router)
    app.include_router(assistant_router)
    try:
        uvicorn.run(app, host='127.0.0.1', port=8002, log_level='warning')
    finally:
        client.close()


def stop(process):
    if process is not None and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def verify():
    configured = settings()
    for port in (8002, 5174):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    environment = os.environ.copy()
    secret = secrets.token_urlsafe(48)
    environment.update({'CUSTOMER_JWT_SECRET': secret, 'PYTHONDONTWRITEBYTECODE': '1'})
    environment['ADVICE_E2E_TOKEN'] = jwt.encode(
        {'customer_id': 'advice-browser-' + secrets.token_hex(8), 'exp': int(time.time()) + 900},
        secret, algorithm='HS256',
    )
    with MongoClient(configured.mongo_uri, serverSelectionTimeoutMS=5000) as client, tempfile.TemporaryDirectory(prefix=".advice-env-", dir=ROOT) as env_dir:
        db = database(client)
        before = snapshot(db)
        api = web = None
        try:
            api = subprocess.Popen([sys.executable, '-m', 'scripts.verify_advice_browser', '--serve'],
                                   cwd=ROOT, env=environment, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            frontend_environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'USER', 'LANG') if key in os.environ}
            frontend_environment['API_PROXY_TARGET'] = API
            frontend_environment['STOCKPILOT_DEMO_ENV_DIR'] = env_dir
            web = subprocess.Popen(['npm', 'run', 'dev', '--', '--host', '127.0.0.1', '--port', '5174', '--strictPort'],
                                   cwd=ROOT / 'frontend', env=frontend_environment, start_new_session=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with httpx.Client(timeout=2) as http:
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    if api.poll() is not None or web.poll() is not None:
                        raise RuntimeError('Advice browser server refused startup.')
                    try:
                        if http.get(UI + '/api/health').status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(.2)
                else:
                    raise RuntimeError('Advice browser startup timeout.')
            run = subprocess.run(['npx', 'playwright', 'test', '--config', 'playwright.advice.config.ts'],
                                 cwd=ROOT / 'frontend', env=environment, capture_output=True, text=True)
            if run.returncode:
                private_output = (run.stdout + run.stderr).replace(environment['ADVICE_E2E_TOKEN'], '<redacted>')
                Path('/tmp/stockpilot-advice-browser-failure.txt').write_text(private_output)
                raise RuntimeError('Advice browser assertions failed; private output withheld.')
            print('Browser: 1 passed; three representative assistant requests.')
            for line in run.stdout.splitlines():
                if 'V8_LLM_STATUS=' in line:
                    print('LLM explanation: ' + ('deterministic fallback' if 'fallback' in line else 'real validated model output'))
        finally:
            stop(web)
            stop(api)
            assert before == snapshot(db), 'E2E persistent documents/indexes changed'
            for port in (8002, 5174):
                with socket.socket() as probe:
                    assert probe.connect_ex(('127.0.0.1', port)) != 0
        print('Mongo E2E integrity PASS; no seed/reset/migration/PG connection; owned listeners stopped.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    try:
        serve() if args.serve else verify()
    except Exception as error:
        print('Advice browser validation failed (' + type(error).__name__ + '); connection/authentication details withheld.')
        raise SystemExit(1)
