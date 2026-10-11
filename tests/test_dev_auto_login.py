import os
from pathlib import Path
import subprocess
import sys

import jwt
import pytest

SCRIPT = Path(__file__).parents[1] / 'scripts/create_customer_token.py'
SECRET = 'test-only-auto-login-secret-at-least-32-bytes'

def generate(*, customer_id='local-client-a', ttl='3600', **overrides):
    env = {**os.environ, 'CUSTOMER_JWT_SECRET': SECRET,
           'MONGO_DATABASE': 'stockpilot', 'MONGO_URI': 'mongodb://127.0.0.1:27017',
           'DEPLOYMENT_ENVIRONMENT': 'development', 'STOCKPILOT_DEMO_ENV_DIR': '',
           'E2E_CUSTOMER_JWT_SECRET': '', **overrides}
    return subprocess.run([sys.executable, str(SCRIPT), customer_id,
                           '--expires-in', ttl, '--dev-auto-login'],
                          env=env, capture_output=True, text=True, timeout=15)

def test_local_dev_generates_expiring_signed_test_identity():
    result = generate()
    assert result.returncode == 0
    claims = jwt.decode(result.stdout.strip(), SECRET, algorithms=['HS256'],
                        options={'require': ['exp', 'customer_id']})
    assert claims['customer_id'] == 'local-client-a'

@pytest.mark.parametrize('overrides', [
    {'MONGO_DATABASE': 'stockpilot_demo'}, {'MONGO_DATABASE': 'stockpilot_e2e'},
    {'MONGO_URI': 'mongodb://remote.example:27017'},
    {'MONGO_URI': 'mongodb+srv://localhost'}, {'MONGO_URI': 'https://localhost'},
    {'DEPLOYMENT_ENVIRONMENT': 'production'}, {'STOCKPILOT_DEMO_ENV_DIR': '/tmp/demo'},
    {'E2E_CUSTOMER_JWT_SECRET': 'test-e2e'}, {'CUSTOMER_JWT_SECRET': ''},
])
def test_non_dev_configuration_refuses_without_output(overrides):
    result = generate(**overrides)
    assert result.returncode != 0
    assert result.stdout == ''
    assert SECRET not in result.stderr

@pytest.mark.parametrize('arguments', [
    {'customer_id': 'other-client'}, {'ttl': '3601'}, {'ttl': '0'}, {'ttl': '-1'},
])
def test_auto_login_identity_and_lifetime_are_bounded(arguments):
    result = generate(**arguments)
    assert result.returncode != 0
    assert result.stdout == ''
    assert SECRET not in result.stderr
