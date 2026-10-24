"""One-off bounded latest-only inspection of two saved FemDom public peers.

No contacts.Search, older message scan, LLM calls, joins, publishers or sends.
Missing audience evidence always results in RESEARCH_ONLY, never a send target.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.icp_service import icp_service
from app.platform_discovery import PlatformCandidate
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_audience_semantic_fit import TelegramAudienceSemanticFit
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import _public_handle
from app.telegram_native_flood_wait import research_flood_wait_until
from app.telegram_research import (
    TelegramSurfaceKind,
    get_telegram_research_connector,
)
from app.telegram_saved_target_bridge import sync_verified_telegram_opportunity

_PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
_PROJECT = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")
_HANDLES = ("a_sfera", "play_hard_community")


async def run(*, store: RuntimeStateStore, connector, product, icp) -> dict:
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(_PROJECT))
    if not isinstance(project, dict) or project.get("deleted_at"):
        raise ValueError("Expected FemDom customer project missing")
    if str(project.get("product_id") or "") != str(_PRODUCT):
        raise ValueError("FemDom customer project/product mismatch")
    if str(getattr(product, "id", "")) != str(_PRODUCT):
        raise ValueError("Unexpected FemDom product")
    if research_flood_wait_until(store) is not None:
        return {"status": "DEFERRED_FLOOD_WAIT", "telegram_reads": 0}

    registry = TelegramCommunityRegistry(store)
    rows = registry.list_for_product(_PRODUCT)
    by_handle = {
        _public_handle(registry.hydrate_metadata(row).get("handle") or row.get("url")): row
        for row in rows
    }
    if any(handle not in by_handle for handle in _HANDLES):
        raise ValueError("One-off peers must already exist in the saved FemDom registry")
    # Inspection is limited to exactly two known peers and ONLY their newest post.
    inspector = getattr(connector, "inspect_latest_known", None)
    if not callable(inspector):
        raise ValueError("Native latest-only known-peer inspection is required")
    inspections = await inspector(list(_HANDLES))
    inspected = {str(item.handle).casefold(): item for item in inspections}
    adapter = CommunityFirstTelegramDiscoveryAdapter(
        research_connector=connector, store=store
    )
    requests = adapter.build_requests(product, icp)
    if not requests:
        raise ValueError("FemDom audience description cannot build research context")
    semantic = TelegramAudienceSemanticFit(store=store, enabled=False)
    report = []

    for handle in _HANDLES:
        outcome = inspected.get(handle)
        snapshot = getattr(outcome, "snapshot", None)
        if getattr(outcome, "status", None) != "VERIFIED" or snapshot is None:
            report.append({"handle": handle, "status": "NATIVE_UNVERIFIED"})
            continue
        if snapshot.username.casefold() != handle:
            report.append({"handle": handle, "status": "HANDLE_MISMATCH"})
            continue
        if len(snapshot.recent_context) > 1:
            report.append({"handle": handle, "status": "LATEST_ONLY_POLICY_VIOLATED"})
            continue
        original = by_handle[handle]
        previous_id = int(registry.hydrate_metadata(original).get("telegram_entity_id") or 0)
        if previous_id and previous_id != snapshot.entity_id:
            report.append({"handle": handle, "status": "ENTITY_ID_MISMATCH"})
            continue

        metadata = adapter._native_metadata(requests[0], snapshot)
        original_fit = original.get("audience_fit") or {}
        direct_registry = original_fit.get("confirmed") is True
        manual_confirmation = (
            direct_registry
            and str(original_fit.get("reason") or "") == "USER_MANUALLY_CONFIRMED"
        )
        verdict = await semantic.evaluate(
            product=product, icp=icp, handle=handle,
            title=snapshot.title, metadata=metadata, cache_only=True,
        )
        direct_llm = verdict.get("status") == "CACHED" and verdict.get("direct_fit") is True
        confirmed = direct_registry or direct_llm
        metadata.update({
            "registry_refresh_attempted": True,
            "registry_refresh_status": "VERIFIED",
            "community_fit_pass": confirmed,
            "registry_confirmed": confirmed,
            "community_fit_reason": (
                str(original_fit.get("reason") or "REGISTRY_CONFIRMED")
                if direct_registry else "CACHED_SEMANTIC_DIRECT_FIT"
                if direct_llm else "PENDING_AUDIENCE_REVIEW"
            ),
            "community_fit_terms": (
                list(original_fit.get("terms") or [])
                if direct_registry else list(verdict.get("evidence") or [])[:4]
            ),
        })
        kind = (
            OpportunityKind.CHANNEL
            if snapshot.kind == TelegramSurfaceKind.CHANNEL
            else OpportunityKind.GROUP
        )
        candidate = PlatformCandidate(
            platform=DistributionPlatform.TELEGRAM,
            kind=kind, canonical_key=f"telegram:{snapshot.entity_id}:community",
            title=snapshot.title, url=snapshot.url, metadata=metadata, hits=[],
        )
        adapter._remember_community(candidate)
        result = sync_verified_telegram_opportunity(
            store, _PRODUCT, candidate, now=datetime.now(UTC)
        )
        report.append({
            "handle": handle,
            "status": result["status"],
            "audience": "DIRECT" if confirmed else "PENDING_REVIEW",
            "cached_semantic": verdict.get("verdict") or verdict.get("status"),
            "cached_semantic_direct_fit": verdict.get("direct_fit") is True,
            "prior_fit_reason": original_fit.get("reason"),
            "manual_confirmation": manual_confirmation,
            "manual_model_disagreement": bool(
                manual_confirmation
                and verdict.get("status") == "CACHED"
                and verdict.get("verdict") != "DIRECT"
            ),
            "research_latest_post_url": result.get("research_latest_post_url"),
            "action_target_url": result.get("action_target_url"),
            "publisher_permission_verified": False,
            "post_read_count": len(snapshot.recent_context),
        })

    return {
        "status": "FEMDOM_LATEST_ONLY_RECONCILIATION_COMPLETE",
        "checked_at": datetime.now(UTC).isoformat(),
        "project_id": str(_PROJECT), "product_id": str(_PRODUCT),
        "telegram_peers_checked": len(inspections),
        "communities": report,
        "new_global_search_calls": 0,
        "llm_new_calls": 0, "telegram_messages_sent": 0,
        "publisher_approvals": 0, "profile_changes": 0,
        "channel_toggle_changes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--project-id", type=UUID, required=True)
    args = parser.parse_args()
    if args.product_id != _PRODUCT or args.project_id != _PROJECT:
        raise ValueError("One-off latest-only inspection limited to the FemDom project")
    result = asyncio.run(run(
        store=get_runtime_store(),
        connector=get_telegram_research_connector(),
        product=product_intake_service.get_product(_PRODUCT),
        icp=icp_service.get(_PRODUCT).icps[0],
    ))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
