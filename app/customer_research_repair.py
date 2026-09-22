from __future__ import annotations

import argparse
import asyncio
import json
from uuid import UUID

from app.audience_intelligence_service import audience_intelligence_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.distribution_types import DistributionPlatform
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair one launch-entitled customer project stranded in NEEDS_INPUT."
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, default=None)
    parser.add_argument("--expected-product-name", default="")
    return parser


async def run(args: argparse.Namespace) -> dict:
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(args.project_id))
    if project is None:
        raise ValueError("Customer project not found")
    product_id_raw = project.get("product_id")
    if not product_id_raw:
        raise ValueError("Customer project has no product_id")
    product_id = UUID(str(product_id_raw))
    if args.expected_product_id is not None and product_id != args.expected_product_id:
        raise ValueError(
            f"Refusing repair: expected product {args.expected_product_id}, found {product_id}"
        )
    product = product_intake_service.get_product(product_id)
    expected_name = str(args.expected_product_name or "").strip().casefold()
    if expected_name and expected_name not in str(product.name or "").casefold():
        raise ValueError(
            f"Refusing repair: product name {product.name!r} does not contain "
            f"{args.expected_product_name!r}"
        )

    before_state = str(project.get("research_state") or "")
    result = await customer_funnel_service.repair_stale_research(args.project_id)
    distribution = audience_intelligence_service.get(product_id)
    telegram = [
        item.model_dump(mode="json")
        for item in distribution.opportunities
        if item.platform == DistributionPlatform.TELEGRAM
    ]
    return {
        "project_id": str(args.project_id),
        "product_id": str(product_id),
        "product_name": product.name,
        "before_research_state": before_state,
        "after_research_state": result.state,
        "opportunity_count": distribution.opportunity_count,
        "research_diagnostics": distribution.diagnostics,
        "telegram_opportunity_count": len(telegram),
        "telegram_opportunities": telegram[:10],
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        payload = asyncio.run(run(args))
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:2000],
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
