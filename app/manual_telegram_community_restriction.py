from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from uuid import UUID

from app.customer_live_opportunities import (
    CUSTOMER_PROJECT_NAMESPACE,
    customer_live_opportunity_service,
)
from app.manual_opportunity_refresh import manual_opportunity_refresh_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_community_restrictions import TelegramCommunityRestrictionMemory
from app.telegram_opportunity_preflight import telegram_opportunity_preflight_service


def _normalized_handle(value: str) -> str:
    return str(value or "").strip().lstrip("@").casefold()


def _project_id_for_product(store: RuntimeStateStore, product_id: UUID) -> UUID:
    matches: list[UUID] = []
    for project in store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
        if project.get("deleted_at"):
            continue
        if str(project.get("product_id") or "") != str(product_id):
            continue
        try:
            matches.append(UUID(str(project["id"])))
        except (KeyError, TypeError, ValueError):
            continue

    unique = sorted(set(matches), key=str)
    if len(unique) != 1:
        raise RuntimeError(
            f"expected exactly one active customer project for product {product_id}; found {len(unique)}"
        )
    return unique[0]


def _opportunity_handle(url: str) -> str | None:
    parsed = telegram_opportunity_preflight_service.parse_comment_target(str(url))
    return parsed.handle.casefold() if parsed is not None else None


def _portfolio_quality(project_id: UUID, community_handle: str) -> dict:
    normalized_handle = _normalized_handle(community_handle)
    opportunities = customer_live_opportunity_service.list_for_project(project_id)
    active_telegram = [
        item
        for item in opportunities
        if item.platform == "TELEGRAM" and item.status == "ACTIVE" and item.freshness != "STALE"
    ]
    matching = [
        item
        for item in active_telegram
        if _opportunity_handle(str(item.url)) == normalized_handle
    ]
    scores = [
        float(item.relevance_score)
        for item in active_telegram
        if item.relevance_score is not None
    ]
    publishability = Counter(item.publishability for item in active_telegram)
    invalid_matching = [
        item
        for item in matching
        if item.publishability != "NO_WRITE_ACCESS"
    ]
    return {
        "active_telegram_opportunities": len(active_telegram),
        "publishability": dict(sorted(publishability.items())),
        "relevance_score_count": len(scores),
        "relevance_score_average": round(sum(scores) / len(scores), 2) if scores else None,
        "relevance_score_min": round(min(scores), 2) if scores else None,
        "relevance_score_max": round(max(scores), 2) if scores else None,
        "relevance_score_ge_70": sum(score >= 70 for score in scores),
        "matching_restricted_community": len(matching),
        "matching_not_no_write_access": len(invalid_matching),
    }


async def seed_refresh_verify(
    *,
    product_id: UUID,
    community_handle: str,
    reason: str,
    refresh: bool,
) -> dict:
    store = get_runtime_store()
    if store.ephemeral:
        raise RuntimeError("refusing to seed Telegram restriction into ephemeral runtime storage")

    project_id = _project_id_for_product(store, product_id)
    memory = TelegramCommunityRestrictionMemory(store)
    restriction = memory.remember(
        project_id,
        community_handle,
        reason=reason,
        restriction_signal="WRITE_RESTRICTED",
    )

    refresh_results: list[dict] = []
    if refresh:
        refresh_results = await manual_opportunity_refresh_service.run_once(
            product_id=product_id,
            force=True,
        )
        if not refresh_results or any(row.get("status") == "FAILED" for row in refresh_results):
            raise RuntimeError(f"opportunity refresh failed: {refresh_results}")

    normalized_handle = _normalized_handle(community_handle)
    stored = memory.get(project_id, normalized_handle)
    if stored is None:
        raise RuntimeError("Telegram restriction was not persisted")

    # A synthetic post id is intentional: restriction memory is checked before any Telegram
    # session/network access, so this proves that every post under the handle short-circuits
    # to NO_WRITE_ACCESS without sending, joining or mutating anything.
    preflight = await telegram_opportunity_preflight_service.check_comment_target(
        project_id,
        f"https://t.me/{normalized_handle}/1",
    )
    if preflight.status != "NO_WRITE_ACCESS":
        raise RuntimeError(
            f"restriction preflight verification failed: expected NO_WRITE_ACCESS, got {preflight.status}"
        )

    quality = _portfolio_quality(project_id, normalized_handle)
    if quality["matching_not_no_write_access"]:
        raise RuntimeError(
            "refreshed portfolio still contains active restricted-community targets without "
            "NO_WRITE_ACCESS"
        )

    return {
        "status": "VERIFIED",
        "product_id": str(product_id),
        "project_id": str(project_id),
        "community_handle": normalized_handle,
        "restriction": stored,
        "preflight": preflight.public_dict(),
        "refresh_results": refresh_results,
        "portfolio_quality": quality,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a confirmed Telegram community write restriction for one product, optionally "
            "refresh discovery, and verify that preflight short-circuits to NO_WRITE_ACCESS."
        )
    )
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--community-handle", required=True)
    parser.add_argument("--reason", default="ACCOUNT_BANNED_IN_COMMUNITY")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Run a forced read-only opportunity discovery refresh before verification.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(
            seed_refresh_verify(
                product_id=args.product_id,
                community_handle=args.community_handle,
                reason=args.reason,
                refresh=args.refresh,
            )
        )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
