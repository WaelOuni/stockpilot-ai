"""Tests explicitly select the controlled local mode before importing the app.

Production defaults remain closed outside pytest. Dedicated integration URI and
identity guards are unchanged and never derive targets from this mode.
"""
import os

os.environ.setdefault("DEPLOYMENT_ENVIRONMENT", "development")
