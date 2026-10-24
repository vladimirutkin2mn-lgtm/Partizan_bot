"""Restore archived public Telegram community IDs, not old post opportunities.

This intentionally does not search, fetch Telegram, join chats, generate
comments, approve audiences or enable publication. Preview is the default.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.saved_discovery_audit import _telegram_target
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle

PRODUCT_ID = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "ops/femdom/live_opportunities_2026-10-08.json"
)
SOURCE = "ARCHIVED_REVIEWED_OPERATIONAL_OPPORTUNITY"
MAX_ARCHIVED_POSTS = 20


def _archived_public_communities(
    records: list[dict],
) -> dict[str, dict[str, object]]:
    """Extract identities only, never inherit stale relevance or permission."""
    if len(records) > MAX_ARCHIVED_POSTS:
        raise ValueError("Historical fixture exceeds bounded import scope")
    peers: dict[str, dict[str, object]] = {}
    for item in records:
        if not isinstance(item, dict):
            continue
        if str(item.get("platform") or "").upper() != "TELEGRAM":
            continue
        if str(item.get("source") or "") != "OPERATIONAL_DISCOVERY":
            continue
        url, community = _telegram_target(item.get("url"))
        if url is None or community is None:
            continue
        handle = community.removeprefix("telegram:")
        if not handle:
            continue
        row = peers.setdefault(
            handle,
            {"handle": handle, "source_post_urls": []},
        )
        urls = row["source_post_urls"]
        if url not in urls:
            urls.append(url)
    return peers


def restore_fixture(
    store: RuntimeStateStore,
    *,
    product_id: UUID,
    commit: bool = False,
    records: list[dict] | None = None,
) -> dict:
    """Find and optionally stage only missing public identities as UNVERIFIED."""
    if product_id != PRODUCT_ID:
        raise ValueError("This one-off is scoped to the existing FemDom product")
    if records is None:
        records = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("Expected an array of archived opportunity records")

    peers = _archived_public_communities(records)
    registry = TelegramCommunityRegistry(store)
    existing = {
        handle
        for entry in registry.list_for_product(product_id)
        if (handle := _public_handle(
            registry.hydrate_metadata(entry).get("handle") or entry.get("url")
        ))
    }
    output = []
    for handle, details in sorted(peers.items()):
        present = handle in existing
        status = "EXISTING" if present else "STAGED" if commit else "PREVIEW_ONLY"
        if commit and not present:
            registry.upsert(
                product_id=product_id,
                canonical_key=f"telegram:{handle}:community",
                title=handle,
                url=f"https://t.me/{handle}",
                kind="CHANNEL",
                metadata={
                    "handle": handle,
                    "source_type": SOURCE,
                    "native_research_status": "UNVERIFIED",
                    "registry_confirmed": False,
                    "community_fit_pass": False,
                    "access_state": "PUBLIC",
                    "actionability": "RESEARCH_ONLY",
                    "action_target_url": None,
                    "action_target_specific": False,
                    "publisher_permission_verified": False,
                    "archived_source_post_urls": list(details["source_post_urls"]),
                    "community_only_reason": "AWAITING_FRESH_NATIVE_VERIFICATION",
                },
            )
            existing.add(handle)
        output.append({
            "handle": handle,
            "status": status,
            "archived_posts": len(details["source_post_urls"]),
            "audience_confirmed_from_archive": False,
            "post_publishable_from_archive": False,
        })
    return {
        "mode": "COMMIT_RESEARCH_ONLY" if commit else "PREVIEW_ONLY",
        "unique_archive_handles": len(peers),
        "newly_staged": sum(row["status"] == "STAGED" for row in output),
        "already_registered": sum(row["status"] == "EXISTING" for row in output),
        "would_stage": sum(row["status"] == "PREVIEW_ONLY" for row in output),
        "telegram_calls": 0,
        "new_search_calls": 0,
        "llm_calls": 0,
        "telegram_send_calls": 0,
        "rows": output,
    }


async def run(*, store: RuntimeStateStore, commit: bool) -> dict:
    product = product_intake_service.get_product(PRODUCT_ID)
    icp = icp_service.get(PRODUCT_ID).icps[0]
    registry = TelegramCommunityRegistry(store)
    before = registry.list_for_product(PRODUCT_ID)
    before_confirmed = sum(
        (row.get("audience_fit") or {}).get("confirmed") is True
        for row in before
    )
    # This is an explicitly approved one-off migration. No native Telegram
    # inspection, publishing permission update or paid LLM call is permitted.
    reconciled = 0
    if commit:
        adapter = CommunityFirstTelegramDiscoveryAdapter(
            use_default_research_connector=False, store=store
        )
        adapter._product_id = PRODUCT_ID
        reconciled = await adapter._reconcile_cached_direct_audiences(product, icp)

    fixture = restore_fixture(store, product_id=PRODUCT_ID, commit=commit)
    after = registry.list_for_product(PRODUCT_ID)
    after_confirmed = sum(
        (row.get("audience_fit") or {}).get("confirmed") is True
        for row in after
    )
    return {
        "status": "HISTORICAL_CACHED_FIT_RECONCILIATION_COMPLETE",
        "commit": commit,
        "product_id": str(PRODUCT_ID),
        "registry_before": len(before),
        "confirmed_before": before_confirmed,
        "cached_direct_promoted": reconciled,
        "registry_after": len(after),
        "confirmed_after": after_confirmed,
        "archive": fixture,
        "telegram_calls": 0,
        "new_search_calls": 0,
        "llm_calls": 0,
        "telegram_send_calls": 0,
        "publication_actions": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recover strictly historical FemDom community identities"
    )
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    if args.product_id != PRODUCT_ID:
        parser.error("Only the approved FemDom product is supported")
    report = asyncio.run(run(store=get_runtime_store(), commit=args.commit))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
