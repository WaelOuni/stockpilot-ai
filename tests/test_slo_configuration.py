import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_all_provisioned_dashboards_are_valid_json():
    for path in (ROOT / "docker" / "grafana" / "dashboards").glob("*.json"):
        dashboard = json.loads(path.read_text())
        assert dashboard["uid"]
        assert len({panel["id"] for panel in dashboard["panels"]}) == len(dashboard["panels"])


def test_slo_rules_and_dashboard_reference_known_metrics():
    rules = yaml.safe_load((ROOT / "docker" / "prometheus-rules.yml").read_text())
    records = rules["groups"][0]["rules"]
    alerts = rules["groups"][1]["rules"]
    assert len(records) == len(alerts) == 5
    names = {record["record"] for record in records}
    for rule in alerts:
        assert rule["for"] == "5m"
        assert rule["expr"].split()[0] in names
    dashboard = json.loads((ROOT / "docker" / "grafana" / "dashboards" / "stockpilot-slo.json").read_text())
    for panel in dashboard["panels"]:
        if panel["id"] in (7, 8):
            continue
        assert any(name in panel["targets"][0]["expr"] for name in names)
    raw = (ROOT / "docker" / "prometheus-rules.yml").read_text()
    import re
    referenced = set(re.findall(r"\b(stockpilot_[a-z_]+)", raw))
    assert referenced == {
        "stockpilot_http_requests_total", "stockpilot_http_request_duration_seconds_bucket",
        "stockpilot_http_request_duration_seconds_count", "stockpilot_orders_finalized_total",
        "stockpilot_orders_failed_total", "stockpilot_llm_errors_total",
        "stockpilot_llm_calls_total", "stockpilot_mongo_order_transactions_total",
    }
    for forbidden in ("request_id", "trace_id", "span_id", "thread_id",
                      "operation_id", "order_id", "product_name"):
        assert forbidden not in raw
    config = yaml.safe_load((ROOT / "docker" / "prometheus.yml").read_text())
    assert "/etc/prometheus/prometheus-rules.yml" in config["rule_files"]


def test_external_availability_configuration():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    exporter = compose["services"]["blackbox-exporter"]
    assert exporter["image"] == "prom/blackbox-exporter:v0.26.0"
    assert "ports" not in exporter
    assert "blackbox-exporter" in compose["services"]["prometheus"]["depends_on"]
    module = yaml.safe_load((ROOT / "docker" / "blackbox.yml").read_text())["modules"]["http_2xx"]
    assert module["prober"] == "http"
    assert module["timeout"] == "5s"
    assert module["http"] == {
        "method": "GET", "preferred_ip_protocol": "ip4",
        "ip_protocol_fallback": False, "follow_redirects": False,
    }
    config = yaml.safe_load((ROOT / "docker" / "prometheus.yml").read_text())
    job = next(job for job in config["scrape_configs"] if job["job_name"] == "stockpilot-blackbox")
    assert job["metrics_path"] == "/probe"
    assert job["scrape_timeout"] == "8s"
    assert job["params"] == {"module": ["http_2xx"]}
    assert job["static_configs"] == [{"targets": ["http://host.docker.internal:8000/health"]}]
    assert job["relabel_configs"] == [
        {"source_labels": ["__address__"], "target_label": "__param_target"},
        {"source_labels": ["__param_target"], "target_label": "instance"},
        {"target_label": "__address__", "replacement": "blackbox-exporter:9115"},
    ]
    rules = yaml.safe_load((ROOT / "docker" / "prometheus-rules.yml").read_text())
    alerts = {rule["alert"]: rule for rule in rules["groups"][2]["rules"]}
    assert alerts["StockPilotUnavailable"]["expr"] == 'probe_success{job="stockpilot-blackbox"} == 0'
    assert alerts["StockPilotUnavailable"]["for"] == "1m"
    assert alerts["StockPilotUnavailable"]["labels"] == {"severity": "critical"}
    assert alerts["BlackboxProbeMissing"]["expr"] == 'absent(probe_success{job="stockpilot-blackbox"})'
    assert alerts["BlackboxProbeMissing"]["for"] == "2m"
    for alert in alerts.values():
        assert alert["annotations"]["summary"]
        assert alert["annotations"]["description"]
    dashboard = json.loads((ROOT / "docker" / "grafana" / "dashboards" / "stockpilot-slo.json").read_text())
    reachability = next(panel for panel in dashboard["panels"] if panel["id"] == 7)
    assert reachability["targets"] == [{"expr": 'probe_success{job="stockpilot-blackbox"}', "instant": True}]
    assert reachability["fieldConfig"]["defaults"]["noValue"] == "UNKNOWN"
