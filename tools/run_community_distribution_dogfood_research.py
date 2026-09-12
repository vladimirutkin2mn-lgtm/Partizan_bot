#!/usr/bin/env python3
"""Run bounded first-party Community Distribution research in production.

This runner is intentionally scoped to PARTIZAN_SELF_DOGFOOD_PRODUCT_ID. It performs
research only: Audience Intelligence discovery (including native Telegram research)
and bounded Reddit public-policy enrichment. It never imports or invokes publishing,
execution, paid activation, managed-distribution fulfillment, or customer autopilot.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.audience_intelligence_service import audience_intelligence_service
from app.config import get_settings
from app.distribution_types import DistributionPlatform
from app.icp_service import icp_service
from app.opportunity_enrichment import opportunity_enrichment_service
from app.product_intake import product_intake_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-reddit", type=int, default=5)
    return parser.parse_args()


def _require_real_production_research() -> None:
    settings = get_settings()
    if settings.app_env.strip().lower() != "production":
        raise RuntimeError("dogfood community research requires APP_ENV=production")
    if settings.runtime_storage.strip().lower() != "database":
        raise RuntimeError("dogfood community research requires database runtime storage")
    if settings.partizan_self_dogfood_product_id is None:
        raise RuntimeError("PARTIZAN_SELF_DOGFOOD_PRODUCT_ID is not configured")
    if settings.search_provider.strip().lower() == "mock":
        raise RuntimeError("real community research refuses mock search provider")
    if settings.telegram_research_provider.strip().lower() != "telethon":
        raise RuntimeError("real Telegram research requires TELETHON provider")
    if not settings.telegram_research_public_ready:
        raise RuntimeError("real Telegram research is not marked public-ready")


async def run(max_reddit: int) -> dict[str, int | str]:
    if not 1 <= max_reddit <= 5:
        raise ValueError("--max-reddit must be between 1 and 5")

    _require_real_production_research()
    settings = get_settings()
    product_id = settings.partizan_self_dogfood_product_id
    assert product_id is not None

    product = product_intake_service.get_product(product_id)
    icp_result = icp_service.get(product_id)

    audience_map = await audience_intelligence_service.discover(product, icp_result)
    telegram = [
        item
        for item in audience_map.opportunities
        if item.platform == DistributionPlatform.TELEGRAM
    ]
    verified_telegram = [
        item
        for item in telegram
        if item.metadata.get("native_research_status") == "VERIFIED"
    ]
    reddit = [
        item
        for item in audience_map.opportunities
        if item.platform == DistributionPlatform.REDDIT
    ]

    reddit_requested = min(max_reddit, len(reddit))
    reddit_enriched = 0
    reddit_partial_failures = 0
    if reddit_requested:
        enrichment = await opportunity_enrichment_service.enrich_product(
            product,
            reddit,
            max_opportunities=reddit_requested,
        )
        reddit_enriched = enrichment.enriched_count
        reddit_partial_failures = enrichment.partial_failure_count

    return {
        "status": "completed",
        "discovery_opportunity_count": audience_map.opportunity_count,
        "telegram_opportunity_count": len(telegram),
        "telegram_verified_native_count": len(verified_telegram),
        "reddit_opportunity_count": len(reddit),
        "reddit_enrichment_requested": reddit_requested,
        "reddit_enrichment_succeeded": reddit_enriched,
        "reddit_enrichment_partial_failures": reddit_partial_failures,
    }


def main() -> int:
    args = parse_args()
    result = asyncio.run(run(args.max_reddit))
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
