"""Read durable states from the parent's explicitly disposable Mongo target."""

import argparse
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from integration_targets import mongo_test_client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "stage",
        choices=[
            "prepared",
            "reserved",
            "cancelled",
            "adjusted",
            "preview",
            "imported",
            "expire",
            "near_expiry",
        ],
    )
    args = parser.parse_args()
    name = os.environ["B2B_BROWSER_DATABASE"]
    if not re.fullmatch(r"stockpilot_b2b_test_[a-f0-9]{32}", name):
        raise RuntimeError("Disposable database required.")
    with mongo_test_client() as client:
        db = client[name]
        a = db.b2b_products.find_one(
            {"organization_id": "fixture-org", "sku": "DRILL-001"}
        )
        b = db.b2b_products.find_one(
            {"organization_id": "fixture-other", "sku": "DRILL-001"}
        )
        assert b["quantity"] == 2 and b["reserved"] == 0
        expected = {
            "prepared": (10, 0),
            "reserved": (10, 1),
            "cancelled": (10, 0),
            "adjusted": (8, 0),
            "preview": (8, 0),
            "imported": (9, 0),
        }
        if args.stage in expected:
            assert (a["quantity"], a["reserved"]) == expected[args.stage]
        if args.stage in {"adjusted", "preview", "imported"}:
            assert db.b2b_stock_movements.count_documents(
                {"organization_id": "fixture-org"}
            ) == (2 if args.stage == "imported" else 1)
        if args.stage == "near_expiry":
            db.bff_sessions.update_many(
                {},
                {
                    "$set": {
                        "absolute_expires_at": datetime.now(UTC) + timedelta(seconds=45)
                    }
                },
            )
        if args.stage == "expire":
            # Explicit fault injection on owned disposable sessions, never a demo.
            db.bff_sessions.update_many(
                {},
                {
                    "$set": {
                        "absolute_expires_at": datetime.now(UTC) - timedelta(seconds=1)
                    }
                },
            )
    print("durable-" + args.stage + "-verified")


if __name__ == "__main__":
    main()
