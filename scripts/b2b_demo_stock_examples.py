"""Reproducible DEMO data and read-only adjustment plans. Never applies a plan.

The qualified VM operator must capture/restore a CURRENT backup before submitting
these commands to the existing authorized stock adjustment service or HTTP route.
No direct quantity update, migration, reset, credential loading or deployment.
"""

import argparse
import csv
import hashlib
from dataclasses import asdict
from pathlib import Path

from stockpilot.domain.entities.tenant_product import TenantProductQuery
from stockpilot.domain.entities.tenant_stock_operation import AdjustTenantStock

PROFILES = {
    "northline": {"minimum_products": 1250, "zero": 25, "low": 20, "prefix": "ADEMO"},
    "atelierpro": {"minimum_products": 250, "zero": 10, "low": 10, "prefix": "BDEMO"},
}


def build_plan(catalogue, request, context, profile):
    """Bounded tenant reads; retain only the few unreserved candidates needed.

    Existing zero/low products count towards the goal, including reserved products.
    Only active unreserved products with >=5 available are candidates for change.
    Each future adjustment has an exact product ID, revision, reason and stable key.
    """
    target = PROFILES[profile]
    inventory = catalogue.search(request, context, TenantProductQuery(limit=1))
    if inventory.total < target["minimum_products"] or inventory.total > 10000:
        raise ValueError("Unexpected catalogue size; refuse to plan DEMO changes.")
    zero = low = active = 0
    candidates = []
    offset = 0
    while True:
        page = catalogue.search_stock(
            request,
            context,
            TenantProductQuery(limit=100, offset=offset, status="active"),
        )
        for item in page.items:
            p, stock = item.product, item.stock
            if (
                p.organization_id != context.organization_id
                or stock.organization_id != context.organization_id
            ):
                raise ValueError("Foreign stock in DEMO plan.")
            active += 1
            zero += stock.available == 0
            low += 1 <= stock.available <= 4
            if (
                stock.reserved == 0
                and stock.available >= 5
                and len(candidates) < target["zero"] + target["low"]
            ):
                candidates.append(item)
        if not page.has_more:
            break
        offset += 100
        if offset > 10000 or not page.items:
            raise ValueError("Unbounded or inconsistent DEMO inventory.")
    missing_zero, missing_low = (
        max(0, target["zero"] - zero),
        max(0, target["low"] - low),
    )
    if len(candidates) < missing_zero + missing_low:
        raise ValueError("Not enough active unreserved products; no changes planned.")
    commands = []
    for i, item in enumerate(candidates[: missing_zero + missing_low]):
        desired = 0 if i < missing_zero else 1 + (i - missing_zero) % 4
        key = hashlib.sha256(
            f"demo-availability-v1:{context.organization_id}:{item.product.product_id}:{item.stock.revision}:{desired}".encode()
        ).hexdigest()
        command = AdjustTenantStock(
            key,
            item.product.product_id,
            desired,
            item.stock.revision,
            f"DEMO availability v1 / {profile} / target available={desired}",
        )
        commands.append(
            {
                "sku": item.product.sku,
                "physical_before": item.stock.quantity,
                "reserved_before": item.stock.reserved,
                "command": asdict(command),
            }
        )
    return {
        "organization_id": context.organization_id,
        "profile": profile,
        "total_products": inventory.total,
        "active_products": active,
        "zero_before": zero,
        "low_before": low,
        "adjustments": commands,
    }


def generated_rows(profile):
    """New-installation CREATE_ONLY input. Re-running never authorizes overwrite."""
    target = PROFILES[profile]
    categories = (
        "perceuses",
        "visseuses",
        "ponceuses",
        "scies",
        "quincaillerie",
        "éclairage",
    )
    for i in range(target["minimum_products"]):
        quantity = (
            0
            if i < target["zero"]
            else 1 + (i - target["zero"]) % 4
            if i < target["zero"] + target["low"]
            else 40 + i % 80
        )
        category = categories[i % len(categories)]
        yield {
            "sku": f"{target['prefix']}-NEW-{i + 1:04}",
            "name": f"{category.capitalize()} DEMO {i + 1:04}",
            "description": "Produit synthétique DEMO",
            "category": category,
            "price_minor": 15500 + i % 7 * 100,
            "currency": "EUR",
            "quantity": quantity,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=PROFILES)
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New directory for CREATE_ONLY CSV batches; never an existing installation",
    )
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
    rows = list(generated_rows(args.profile))
    for i in range(0, len(rows), 100):
        with (args.output / f"create-only-{i // 100 + 1:02}.csv").open(
            "x", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows[i : i + 100])
    print(
        f"Generated {len(rows)} synthetic products in bounded CREATE_ONLY batches; nothing applied."
    )


if __name__ == "__main__":
    main()
