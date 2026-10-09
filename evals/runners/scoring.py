import json
import math
import unicodedata
from pathlib import Path

from stockpilot.domain.enums.product_category import ProductCategory


def normalize(value):
    if value is None or not isinstance(value, str):
        return value
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def matches(expected, actual, comparison="normalized"):
    if comparison == "exact":
        return type(expected) is type(actual) and expected == actual
    if comparison != "normalized":
        raise ValueError(f"Unknown comparison mode: {comparison}")
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and abs(expected - actual) < 1e-6
    return normalize(expected) == normalize(actual)


def accuracy(results):
    values = list(results)
    return sum(values) / len(values) if values else None


def recall(sources, expected, k):
    relevant = set(expected)
    if not relevant or k < 1:
        raise ValueError("Recall requires relevant sources and positive k")
    return len(set(sources[:k]) & relevant) / len(relevant)


def reciprocal_rank(sources, expected):
    return next((1 / rank for rank, source in enumerate(sources, 1) if source in expected), 0.0)


def grounded(product, documents):
    if not product:
        return None
    needle = normalize(product)
    # Product documents use the canonical product as their first line.
    return any(needle == normalize(doc.content.splitlines()[0])
               for doc in documents if doc.content.splitlines())


def load_cases(path, kind):
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"{path}: expected a nonempty array")
    ids = set()
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in ids:
            raise ValueError(f"{path}: invalid or duplicate case id")
        ids.add(case["id"])
        text_key = "query" if kind == "rag" else "message"
        if not isinstance(case.get(text_key), str) or not case[text_key].strip():
            raise ValueError(f"{path}: missing {text_key}")
        if kind in ("intent", "order"):
            expected = case.get("expected")
            if not isinstance(expected, dict) or not isinstance(expected.get("intent"), str) or expected["intent"] not in {
                "ORDER", "STOCK_CHECK", "PRODUCT_ADVICE", "UNKNOWN",
            }:
                raise ValueError(f"{path}: invalid expected intent")
            if set(expected) - {"intent", "product_name", "raw_product", "quantity", "category", "budget", "usage"}:
                raise ValueError(f"{path}: unknown expected field")
            for field in ("product_name", "raw_product", "usage"):
                if field in expected and expected[field] is not None and not isinstance(expected[field], str):
                    raise ValueError(f"{path}: invalid {field}")
            if expected.get("category") is not None and (
                not isinstance(expected["category"], str) or expected["category"] not in {
                    category.value for category in ProductCategory
                }
            ):
                raise ValueError(f"{path}: invalid category")
            quantity = expected.get("quantity")
            if quantity is not None and type(quantity) is not int:
                raise ValueError(f"{path}: quantity must be an integer")
            budget = expected.get("budget")
            if budget is not None and (
                type(budget) not in (int, float) or not math.isfinite(budget) or budget < 0
            ):
                raise ValueError(f"{path}: invalid budget")
            comparison = case.get("comparison", {})
            if not isinstance(comparison, dict):
                raise ValueError(f"{path}: invalid comparison")
            for field, mode in comparison.items():
                if field not in expected or mode not in ("exact", "normalized"):
                    raise ValueError(f"{path}: invalid comparison")
        elif kind == "rag":
            sources = case.get("expected_sources")
            if not isinstance(sources, list) or not sources or any(not isinstance(s, str) or not s for s in sources):
                raise ValueError(f"{path}: invalid expected sources")
            if len(set(sources)) != len(sources):
                raise ValueError(f"{path}: duplicate expected source")
        elif kind == "recommendation":
            if "expected_product" not in case or not (
                case["expected_product"] is None or isinstance(case["expected_product"], str)
            ):
                raise ValueError(f"{path}: invalid expected product")
        else:
            raise ValueError(f"Unknown dataset: {kind}")
    return cases


def regressions(scores, baseline, tolerance=0.03):
    if type(tolerance) not in (int, float) or not math.isfinite(tolerance) or tolerance < 0 or tolerance > 1:
        raise ValueError("Tolerance must be between zero and one")
    reference = baseline.get("metrics")
    if not isinstance(reference, dict) or not reference:
        raise ValueError("Baseline has no measured metrics; capture a local run first")
    failures = []
    for metric, expected in reference.items():
        if type(expected) not in (int, float) or not math.isfinite(expected) or not 0 <= expected <= 1:
            raise ValueError(f"Invalid baseline score: {metric}")
        actual = scores.get(metric)
        if actual is None or actual + tolerance + 1e-12 < expected:
            failures.append({"metric": metric, "baseline": expected, "actual": actual})
    return failures
