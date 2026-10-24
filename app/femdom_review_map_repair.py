"""Conservative one-off repair for manually reviewed FemDom Telegram map gaps.

Never decides audience fit, invents native evidence or grants Telegram rights.
Existing duplicate opportunities are retained and reported, not silently merged.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, NAMESPACE_URL, uuid5

from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_operator_telegram_review import EVIDENCE, PRODUCT
from app.runtime_store import RuntimeStateStore
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_saved_target_bridge import _handle, _utc

_MAP_NS = "audience_distribution_map"
_OPP_NS = "audience_distribution_opportunity"


def seed_missing_reviewed_opportunity(
    store: RuntimeStateStore, *, product_id: UUID, icp_id: UUID,
    candidate: object, now: datetime | None = None,
) -> dict:
    """Create a *research-only* map row only when one verified peer is missing.

    This is opt-in from the bounded operator workflow, never generic discovery.
    Duplicate rows or conflicting identities remain blocked for human review.
    """
    if product_id != PRODUCT:
        return {"status": "PRODUCT_SCOPE_BLOCKED"}
    observed = getattr(candidate, "metadata", None)
    if not isinstance(observed, dict):
        return {"status": "INVALID_NATIVE_EVIDENCE"}
    handle = _handle(observed, getattr(candidate, "url", None))
    if not handle:
        return {"status": "INVALID_PUBLIC_HANDLE"}
    reviewed = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    if reviewed.get("product_id") != str(product_id) or handle not in {
        str(row.get("handle") or "").casefold()
        for row in reviewed.get("entries", [])
        if row.get("audience") == "DIRECT"
    }:
        return {"status": "NOT_MANUALLY_REVIEWED"}

    when = now or datetime.now(UTC)
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    when = when.astimezone(UTC)
    inspected = _utc(observed.get("source_checked_at"))
    entity_id = int(observed.get("telegram_entity_id") or 0)
    if (
        observed.get("registry_refresh_attempted") is not True
        or observed.get("registry_refresh_status") != "VERIFIED"
        or observed.get("native_research_status") != "VERIFIED"
        or inspected is None or not timedelta(0) <= when - inspected <= timedelta(minutes=30)
        or entity_id <= 0
        or getattr(candidate, "kind", None) not in (OpportunityKind.CHANNEL, OpportunityKind.GROUP)
    ):
        return {"status": "FRESH_NATIVE_EVIDENCE_REQUIRED"}

    registry = TelegramCommunityRegistry(store)
    matches_registry = [
        row for row in registry.list_for_product(product_id)
        if _handle(registry.hydrate_metadata(row), row.get("url")) == handle
    ]
    if len(matches_registry) != 1:
        return {"status": "REGISTRY_IDENTITY_AMBIGUOUS"}
    row = matches_registry[0]
    meta = registry.hydrate_metadata(row)
    if (
        (row.get("audience_fit") or {}).get("confirmed") is not True
        or str((row.get("audience_fit") or {}).get("reason") or "") != "USER_MANUALLY_CONFIRMED"
        or int(meta.get("telegram_entity_id") or 0) != entity_id
    ):
        return {"status": "REGISTRY_REVIEW_OR_ENTITY_MISMATCH"}

    raw = store.get(_MAP_NS, str(product_id))
    if not isinstance(raw, dict):
        return {"status": "SAVED_MAP_MISSING"}
    try:
        mapping = AudienceDistributionMapView.model_validate(raw)
    except Exception:
        return {"status": "SAVED_MAP_INVALID"}

    existing = [
        opp for opp in mapping.opportunities
        if opp.platform == DistributionPlatform.TELEGRAM
        and _handle(opp.metadata, opp.url) == handle
    ]
    if len(existing) > 1:
        return {"status": "DUPLICATE_SAVED_OPPORTUNITIES", "matches": len(existing)}
    if existing:
        return {"status": "ALREADY_PRESENT", "matches": 1}
    if icp_id not in {opp.icp_id for opp in mapping.opportunities}:
        return {"status": "ICP_NOT_IN_SAVED_MAP"}
    stable_id = uuid5(NAMESPACE_URL, f"partizan:{product_id}:telegram:{entity_id}:operator-review")
    if store.get(_OPP_NS, str(stable_id)) is not None:
        return {"status": "OPPORTUNITY_ID_COLLISION"}

    # Even if a current post is commentable, a missing historical map row is
    # *never* created as a READY action or as an approved account target.
    metadata = {
        "handle": handle, "telegram_entity_id": entity_id,
        "native_research_status": "VERIFIED",
        "source_checked_at": inspected.isoformat(),
        "research_last_native_checked_at": inspected.isoformat(),
        "community_fit_pass": True,
        "community_fit_reason": "USER_MANUALLY_CONFIRMED",
        "research_audience_status": "DIRECT_CONFIRMED",
        "surface_capabilities": dict(observed.get("surface_capabilities") or {}),
        "publisher_permission_verified": False,
        "action_target_url": None, "action_target_specific": False,
        "actionability": "RESEARCH_ONLY",
        "community_only_reason": "RECOVERED_RESEARCH_ONLY_REQUIRES_NATIVE_PREFLIGHT",
        "source": "BOUNDED_FEMDOM_OPERATOR_RECONCILIATION",
    }
    metadata["surface_capabilities"]["publisher_permission_verified"] = False
    opportunity = DistributionOpportunityView(
        id=stable_id, icp_id=icp_id,
        platform=DistributionPlatform.TELEGRAM,
        kind=candidate.kind,
        canonical_key=f"telegram:{entity_id}:community",
        title=str(getattr(candidate, "title", None) or row.get("title") or handle)[:300],
        url=str(getattr(candidate, "url", None) or row.get("url") or f"https://t.me/{handle}"),
        metadata=metadata,
    )
    updated = AudienceDistributionMapView.model_validate(mapping.model_copy(update={
        "opportunities": [*mapping.opportunities, opportunity],
        "opportunity_count": len(mapping.opportunities) + 1,
    }).model_dump(mode="json"))
    # In a single, restricted one-off process. No user action, comment or join.
    store.put(_OPP_NS, str(stable_id), opportunity.model_dump(mode="json"))
    store.put(_MAP_NS, str(product_id), updated.model_dump(mode="json"))
    return {
        "status": "SEEDED_RESEARCH_ONLY", "matches": 0,
        "opportunity_id": str(stable_id),
        "publisher_permission_verified": False,
    }
