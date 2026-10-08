from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.customer_live_opportunities import (
    CustomerLiveOpportunityUpsert,
    customer_live_opportunity_service,
)
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

CUSTOMER_LIVE_OPPORTUNITY_SEED_NAMESPACE = "customer_live_opportunity_seed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply one reviewed, idempotent set of operational acquisition opportunities "
            "to an exact customer workspace without executing any external action."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--seed-id", required=True)
    parser.add_argument("--seed-file", type=Path, required=True)
    return parser


def _product_name_matches(actual_name: str, expected_name: str) -> bool:
    actual = " ".join(str(actual_name or "").split()).casefold()
    expected = " ".join(str(expected_name or "").split()).casefold()
    return bool(expected) and expected in actual


def _load_entries(path: Path) -> list[CustomerLiveOpportunityUpsert]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Live-opportunity seed file must contain a non-empty JSON list")
    return [CustomerLiveOpportunityUpsert.model_validate(item) for item in payload]


def run(args: argparse.Namespace) -> dict:
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(args.project_id))
    if project is None or project.get("deleted_at"):
        raise ValueError("Exact customer project is not available")
    if str(project.get("product_id") or "") != str(args.expected_product_id):
        raise ValueError("Customer project product id does not match the reviewed seed")

    product = product_intake_service.get_product(args.expected_product_id)
    if not _product_name_matches(product.name, args.expected_product_name):
        raise ValueError("Customer project product name does not match the reviewed seed")

    seed_id = args.seed_id.strip()
    if not seed_id:
        raise ValueError("Seed id is required")
    marker_key = f"{args.project_id}:{seed_id}"
    existing = store.get(CUSTOMER_LIVE_OPPORTUNITY_SEED_NAMESPACE, marker_key)
    if existing is not None:
        return {**existing, "status": "ALREADY_APPLIED"}

    entries = _load_entries(args.seed_file)
    applied_ids: list[str] = []
    for entry in entries:
        view = customer_live_opportunity_service.upsert(args.project_id, entry)
        applied_ids.append(view.opportunity_id)

    marker = {
        "status": "APPLIED",
        "seed_id": seed_id,
        "project_id": str(args.project_id),
        "product_id": str(args.expected_product_id),
        "product_name": product.name,
        "seed_file": str(args.seed_file),
        "opportunity_ids": applied_ids,
        "opportunity_count": len(applied_ids),
        "external_action_executed": False,
        "applied_at": datetime.now(UTC).isoformat(),
    }
    if not store.put_if_absent(
        CUSTOMER_LIVE_OPPORTUNITY_SEED_NAMESPACE,
        marker_key,
        marker,
    ):
        existing = store.get(CUSTOMER_LIVE_OPPORTUNITY_SEED_NAMESPACE, marker_key)
        if existing is not None:
            return {**existing, "status": "ALREADY_APPLIED"}
        raise RuntimeError("Live-opportunity seed marker could not be persisted")
    return marker


def main() -> int:
    args = build_parser().parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"error_type": type(exc).__name__, "error": str(exc)[:2000]},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
