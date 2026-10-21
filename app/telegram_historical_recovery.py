"""Recover previously researched public Telegram communities without paid discovery.

This command does not call global Telegram search, any model, publisher preflight,
join, send, or change Growth Mandates. It is safe for research-only projects.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.audience_intelligence_service import InMemoryAudienceIntelligenceService
from app.community_first_telegram_adapter import CommunityFirstTelegramDiscoveryAdapter
from app.distribution_types import DistributionPlatform
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_legacy_registry_bootstrap import (
    _public_handle,
    stage_legacy_telegram_communities,
)
from app.telegram_native_flood_wait import research_flood_wait_until
from app.telegram_research import get_telegram_research_connector

_PROJECTS = "customer_acquisition_projects"
_MAX_COMMUNITIES = 12
_APPROVED_PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
_APPROVED_PROJECT = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")
_APPROVED_HANDLES = frozenset((
    "znakomstvachannel", "poiskfanfikov", "play_hard_community",
    "findom_gospoja0", "a_sfera", "fem_tut",
    "moscowellebdsm1", "poiskrabovirabin", "bizarre_afisha",
))
_MAX_TARGET_AGE = timedelta(days=14)
_NATIVE_FIELDS = {
    "action_target_url", "action_target_specific", "action_target_resolution",
    "action_target_quality", "action_target_mode", "actionability",
    "native_research_status", "source_checked_at", "target_published_at",
    "target_age_days", "recent_context", "surface_capabilities",
    "surface_kind", "telegram_entity_id", "about", "linked_discussion_id",
    "comment_surface", "reply_surface", "standalone_post_surface",
    "community_only_reason", "community_fit_pass", "registry_confirmed",
    "community_fit_reason", "community_fit_terms", "access_state",
}


def _handle(opportunity) -> str | None:
    metadata = opportunity.metadata if isinstance(opportunity.metadata, dict) else {}
    return _public_handle(metadata.get("handle") or opportunity.url)


def _quarantine(opportunity):
    """Old map READY/target values are historical evidence, not native access."""
    metadata = {key: value for key, value in opportunity.metadata.items()
                if key not in _NATIVE_FIELDS}
    metadata.update({
        "native_research_status": "UNVERIFIED",
        "community_fit_pass": False,
        "registry_confirmed": False,
        "actionability": "RESEARCH_ONLY",
        "action_target_url": None,
        "action_target_specific": False,
        "community_only_reason": "AWAITING_FRESH_NATIVE_VERIFICATION",
        "publisher_permission_verified": False,
    })
    return opportunity.model_copy(update={"metadata": metadata})


def _native_target_valid(metadata: dict, handle: str, *, now: datetime) -> bool:
    if metadata.get("native_research_status") != "VERIFIED":
        return False
    if metadata.get("community_fit_pass") is not True:
        return False
    if metadata.get("action_target_specific") is not True:
        return False
    mode = str(metadata.get("action_target_mode") or "")
    if mode not in {"CHANNEL_COMMENT", "GROUP_REPLY"}:
        return False
    target = str(metadata.get("action_target_url") or "")
    if not target.lower().startswith(f"https://t.me/{handle}/"):
        return False
    messages = [item for item in metadata.get("recent_context") or []
                if isinstance(item, dict) and str(item.get("message_id") or "").isdigit()]
    if not messages:
        return False
    latest = max(messages, key=lambda item: int(item["message_id"]))
    if str(latest.get("url") or "").rstrip("/") != target.rstrip("/"):
        return False
    if not str(latest.get("text") or "").strip():
        return False
    try:
        checked = datetime.fromisoformat(
            str(metadata.get("source_checked_at") or "").replace("Z", "+00:00")
        )
        published = datetime.fromisoformat(
            str(latest.get("published_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if checked.tzinfo is None or published.tzinfo is None:
        return False
    checked, published = checked.astimezone(UTC), published.astimezone(UTC)
    return (
        timedelta(0) <= now - checked <= timedelta(minutes=30)
        and timedelta(0) <= now - published <= _MAX_TARGET_AGE
    )


def _recover_opportunity(opportunity, candidate, *, now: datetime):
    """Keep old opportunity ID while replacing every stale Telegram action field."""
    handle = _handle(opportunity)
    if not handle:
        return _quarantine(opportunity)
    metadata = _quarantine(opportunity).metadata
    observed = candidate.metadata if isinstance(candidate.metadata, dict) else {}
    if (
        observed.get("registry_refresh_attempted") is not True
        or observed.get("registry_refresh_status") != "VERIFIED"
    ):
        return opportunity.model_copy(update={"metadata": metadata})
    metadata.update({
        key: value for key, value in observed.items()
        if key in _NATIVE_FIELDS or key in {"handle", "last_activity_at",
                                             "member_count", "opportunity_scope"}
    })
    metadata["publisher_permission_verified"] = False
    if not _native_target_valid(metadata, handle, now=now):
        metadata.update({
            "actionability": "RESEARCH_ONLY",
            "action_target_url": None,
            "action_target_specific": False,
            "community_only_reason": (
                "LATEST_POST_NOT_CONFIRMED" if observed.get("community_fit_pass") is True
                else "AUDIENCE_FIT_UNCONFIRMED"
            ),
        })
    else:
        metadata["actionability"] = "NEEDS_PREFLIGHT"
    return opportunity.model_copy(update={
        "metadata": metadata,
        "kind": candidate.kind,
        "title": candidate.title,
        "url": candidate.url,
    })


async def recover(
    *, store: RuntimeStateStore, project_id: UUID, product_id: UUID,
    product, icps: list, connector, max_groups: int = _MAX_COMMUNITIES,
    authorized_oneoff: bool = False,
) -> dict:
    if not 1 <= max_groups <= _MAX_COMMUNITIES:
        raise ValueError("max_groups must be between 1 and 12")
    project = store.get(_PROJECTS, str(project_id))
    if not isinstance(project, dict) or project.get("deleted_at"):
        raise ValueError("Project does not exist or has been deleted")
    if str(project.get("product_id") or "") != str(product_id):
        raise ValueError("Project/product mismatch")
    mode = (project.get("channel_preferences") or {}).get("TELEGRAM")
    if authorized_oneoff:
        if project_id != _APPROVED_PROJECT or product_id != _APPROVED_PRODUCT:
            raise ValueError("One-off authorization applies only to the FemDom project")
        if max_groups != 9:
            raise ValueError("One-off authorization requires exactly nine peers")
    elif mode != "RESEARCH_ONLY":
        raise ValueError("Telegram must stay RESEARCH_ONLY without explicit authorization")

    service = InMemoryAudienceIntelligenceService(store)
    saved = service.get(product_id)
    if authorized_oneoff:
        handles = {_handle(item) for item in saved.opportunities
                   if item.platform == DistributionPlatform.TELEGRAM}
        handles.discard(None)
        if handles != _APPROVED_HANDLES:
            raise ValueError("Historic audience differs from nine approved FemDom peers")
    registry = TelegramCommunityRegistry(store)
    before = registry.list_for_product(product_id)
    if authorized_oneoff:
        registered = {
            _public_handle(registry.hydrate_metadata(item).get("handle") or item.get("url"))
            for item in before
        }
        if registered - _APPROVED_HANDLES:
            raise ValueError("Registry contains peers outside approved scope")
    migration = stage_legacy_telegram_communities(store, product_id)
    after = registry.list_for_product(product_id)

    # Quarantine stale legacy target records before any native calls; no publishing.
    unverified = {
        _public_handle(registry.hydrate_metadata(row).get("handle") or row.get("url"))
        for row in after
        if (row.get("audience_fit") or {}).get("confirmed") is not True
    }
    for row in saved.opportunities:
        if row.platform == DistributionPlatform.TELEGRAM and _handle(row) in unverified:
            service.update_opportunity(_quarantine(row))

    checked: dict[str, dict] = {}
    if connector is None or not icps:
        mode = "NATIVE_CONNECTOR_OR_ICP_UNAVAILABLE"
    else:
        mode = "NATIVE_KNOWN_PEER_VERIFICATION"
        adapter = CommunityFirstTelegramDiscoveryAdapter(
            research_connector=connector, store=store
        )
        # The existing rotating adapter bounds native requests to 3 public peers.
        for _ in range((min(max_groups, len(after)) + 2) // 3):
            if research_flood_wait_until(store) is not None:
                mode = "DEFERRED_FLOOD_WAIT"
                break
            candidates = await adapter.refresh_registered(product, icps)
            attempted = [
                candidate for candidate in candidates
                if candidate.metadata.get("registry_refresh_attempted") is True
            ]
            fresh = []
            for candidate in attempted:
                handle = _public_handle(candidate.metadata.get("handle") or candidate.url)
                if handle and handle not in checked:
                    fresh.append((handle, candidate))
            if not fresh:
                break
            for handle, candidate in fresh:
                status = str(candidate.metadata.get("registry_refresh_status") or "")
                checked[handle] = {
                    "status": status,
                    "fit": candidate.metadata.get("community_fit_pass") is True
                    if status == "VERIFIED" else False,
                    "target_url": None,
                    "specific_target": False,
                    "reason": str(candidate.metadata.get("community_fit_reason") or ""),
                }
                if status != "VERIFIED":
                    continue
                for opportunity in service.get(product_id).opportunities:
                    if (
                        opportunity.platform == DistributionPlatform.TELEGRAM
                        and _handle(opportunity) == handle
                    ):
                        upgraded = _recover_opportunity(
                            opportunity, candidate, now=datetime.now(UTC)
                        )
                        service.update_opportunity(upgraded)
                        if upgraded.metadata.get("action_target_specific") is True:
                            checked[handle]["target_url"] = upgraded.metadata.get(
                                "action_target_url"
                            )
                            checked[handle]["specific_target"] = True
            if len(checked) >= max_groups:
                break

    return {
        "status": "HISTORICAL_RESEARCH_RECOVERY_COMPLETE",
        "mode": mode,
        "oneoff_authorized": authorized_oneoff,
        "channel_preference_unchanged": True,
        "product_id": str(product_id),
        "project_id": str(project_id),
        "migration": migration,
        "registry_before": len(before),
        "registry_after": len(after),
        "native_checked": len(checked),
        "native_verified": sum(x["status"] == "VERIFIED" for x in checked.values()),
        "audience_fit_confirmed": sum(x["fit"] for x in checked.values()),
        "specific_targets_retained": sum(x["specific_target"] for x in checked.values()),
        "communities": checked,
        "effects": {
            "paid_search_calls": 0,
            "global_telegram_search_calls": 0,
            "telegram_sends": 0,
            "joins": 0,
            "profile_changes": 0,
            "publication_approvals": 0,
        },
    }


def inspect_prerequisites(store: RuntimeStateStore, product_id: UUID, project_id: UUID) -> dict:
    """Read-only diagnostics; no model, connector, or external requests."""
    project = store.get(_PROJECTS, str(project_id)) or {}
    audience = store.get("audience_distribution_map", str(product_id)) or {}
    icp_record = store.get("icp_generation", str(product_id)) or {}
    product = store.get("product_profile", str(product_id)) or {}
    return {
        "status": "HISTORICAL_RESEARCH_PREFLIGHT",
        "project_present": bool(project),
        "project_matches_product": str(project.get("product_id") or "") == str(product_id),
        "channel_mode": str((project.get("channel_preferences") or {}).get("TELEGRAM") or "MISSING"),
        "audience_map_present": bool(audience),
        "audience_opportunity_count": len(audience.get("opportunities") or []),
        "stored_icp_present": bool(icp_record),
        "stored_icp_count": len(icp_record.get("icps") or []),
        "stored_product_present": bool(product),
        "registry_count": len(TelegramCommunityRegistry(store).list_for_product(product_id)),
        "read_only": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover saved Telegram audiences safely")
    parser.add_argument("--product-id", type=UUID, required=True)
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--max-groups", type=int, default=_MAX_COMMUNITIES)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--authorized-oneoff", action="store_true")
    args = parser.parse_args()
    try:
        store = get_runtime_store()
        if args.preflight_only:
            result = inspect_prerequisites(store, args.product_id, args.project_id)
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        result = asyncio.run(recover(
            store=store, project_id=args.project_id, product_id=args.product_id,
            product=product_intake_service.get_product(args.product_id),
            icps=icp_service.get(args.product_id).icps[:1],
            connector=get_telegram_research_connector(),
            max_groups=args.max_groups, authorized_oneoff=args.authorized_oneoff,
        ))
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (KeyError, ValueError, TypeError) as exc:
        print(json.dumps({
            "status": "HISTORICAL_RESEARCH_RECOVERY_FAILED",
            "error_type": type(exc).__name__,
            "error": str(exc)[:250],
        }, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
