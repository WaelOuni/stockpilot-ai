from pathlib import Path
import re

from stockpilot.infrastructure.config.settings import Settings


ROOT = Path(__file__).resolve().parents[1]


def test_ignore_rules_exclude_runtime_without_excluding_golden_data():
    patterns = set((ROOT / ".gitignore").read_text().splitlines())
    assert {
        ".venv/", "venv/", "__pycache__/", "*.py[cod]", ".pytest_cache/",
        ".env", ".env.*", "!.env.example", ".idea/", ".vscode/", "*.iml",
        "logs/*", "!logs/.gitkeep", "*.log", "stockpilot-before-rs.archive",
        "eval-results*.json", "historical-results.json", "enriched-results.json",
        "dist/", "build/", "*.egg-info/", "*.pem", "*.key", "*.p12", "*.pfx",
    } <= patterns
    assert not any(pattern.startswith("evals/") for pattern in patterns)


def test_environment_example_uses_existing_settings_only():
    assert b"\r" not in (ROOT / ".env.example").read_bytes()
    text = (ROOT / ".env.example").read_text()
    variables = re.findall(r"^([A-Z_]+)=", text, re.MULTILINE)
    assert variables
    assert {variable.lower() for variable in variables} <= Settings.model_fields.keys()
    assert "MONGO_URI='mongodb://" in text  # Shell sourcing must preserve the URI ampersand.
