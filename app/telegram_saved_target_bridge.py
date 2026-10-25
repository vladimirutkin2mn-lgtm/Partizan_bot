"""Carry verified native Telegram snapshots into historical audience opportunities.

The registry is the research source; the persisted audience map feeds the
drafting and customer preview path. Synchronization is bounded to one unique
saved opportunity and NEVER upgrades uncertain audiences or publisher rights.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import ValidationError

from app.distribution_schemas import AudienceDistributionMapView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import RuntimeStateStore

_MAP_NS = "audience_distribution_map"
_OPPORTUNITY_NS = "audience_distribution_opportunity"
_MAX_NATIVE_CHECK_AGE = timedelta(minutes=30)
_MAX_POST_AGE = timedelta(days=14)
_LINK = re.compile(r"^https://t\.me/([a-zA-Z][a-zA-Z0-9_]{4,31})/(\d+)$")
_NATIVE_KEYS = {
    "telegram_entity_id", "handle", "native_research_status", "source_checked_at",
    "last_activity_at", "member_count", "about", "recent_context",
    "surface_capabilities", "linked_discussion_id", "surface_kind",
    "source_post_category", "conversation_signals",
    "community_fit_reason", "community_fit_pass", "community_fit_terms",
    "registry_confirmed", "semantic_audience_fit", "community_commercial_rules",
    "action_target_mode",
    "action_target_resolution", "action_target_quality", "target_age_days",
    "target_published_at",
}


def _utc(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _message(metadata: dict) -> dict | None:
    messages = [
        msg for msg in (metadata.get("recent_context") or [])
        if isinstance(msg, dict)
        and str(msg.get("message_id") or "").isdigit()
    ]
    return max(messages, key=lambda item: int(item["message_id"]), default=None)


def _handle(metadata: dict, url: object) -> str | None:
    value = str(metadata.get("handle") or "").strip().lstrip("@").casefold()
    if not value:
        parsed = urlsplit(str(url or ""))
        if parsed.hostname not in {"t.me", "telegram.me"}:
            return None
        value = parsed.path.strip("/").split("/")[0].casefold()
    return value if re.fullmatch(r"[a-z][a-z0-9_]{4,31}", value) else None


def _actionable_latest(
    metadata: dict, handle: str, kind: OpportunityKind, now: datetime,
) -> bool:
    if metadata.get("native_research_status") != "VERIFIED":
        return False
    if metadata.get("community_fit_pass") is not True:
        return False
    caps = metadata.get("surface_capabilities") or {}
    if not isinstance(caps, dict):
        return False
    mode = str(metadata.get("action_target_mode") or "")
    if kind == OpportunityKind.CHANNEL:
        if mode != "CHANNEL_COMMENT" or caps.get("comment") != "AVAILABLE":
            return False
    elif kind == OpportunityKind.GROUP:
        if mode == "STANDALONE_POST":
            # For active group chats, a message is addressed to the group,
            # not to whichever member happened to write the last message.
            checked = _utc(metadata.get("source_checked_at"))
            url = str(metadata.get("action_target_url") or "").rstrip("/")
            return bool(
                caps.get("standalone_post") == "AVAILABLE"
                and url == f"https://t.me/{handle}"
                and metadata.get("action_target_specific") is False
                and checked is not None
                and timedelta(0) <= now - checked <= _MAX_NATIVE_CHECK_AGE
            )
        if mode != "GROUP_REPLY" or caps.get("reply") != "AVAILABLE":
            return False
    else:
        return False
    latest = _message(metadata)
    if latest is None or not str(latest.get("text") or "").strip():
        return False
    url = str(latest.get("url") or "").strip().rstrip("/")
    target = str(metadata.get("action_target_url") or "").strip().rstrip("/")
    if not target or target != url or metadata.get("action_target_specific") is not True:
        return False
    match = _LINK.fullmatch(url)
    if not match or match.group(1).casefold() != handle:
        return False
    if int(match.group(2)) != int(latest["message_id"]):
        return False
    checked, published = _utc(metadata.get("source_checked_at")), _utc(
        latest.get("published_at")
    )
    if checked is None or published is None:
        return False
    return (
        timedelta(0) <= now - checked <= _MAX_NATIVE_CHECK_AGE
        and timedelta(0) <= now - published <= _MAX_POST_AGE
    )


def sync_verified_telegram_opportunity(
    store: RuntimeStateStore,
    product_id: UUID,
    candidate: object,
    *,
    now: datetime | None = None,
) -> dict:
    """Persist one inspected native peer into ONE historical map opportunity.

    A research URL remains visible even for an adjacent/unrelated audience, but
    cannot be mistaken for an authorized action target.
    """
    observed = getattr(candidate, "metadata", None)
    if not isinstance(observed, dict):
        return {"status": "NO_NATIVE_METADATA"}
    if not (
        observed.get("registry_refresh_attempted") is True
        and observed.get("registry_refresh_status") == "VERIFIED"
        and observed.get("native_research_status") == "VERIFIED"
    ):
        return {"status": "NO_FRESH_NATIVE_INSPECTION"}
    handle = _handle(observed, getattr(candidate, "url", None))
    if not handle:
        return {"status": "NO_PUBLIC_HANDLE"}

    raw = store.get(_MAP_NS, str(product_id))
    if not isinstance(raw, dict):
        return {"status": "NO_SAVED_AUDIENCE_MAP"}
    try:
        mapping = AudienceDistributionMapView.model_validate(raw)
    except ValidationError:
        # Some legacy discovery tests/products stage minimal research maps
        # before the full audience map schema is generated. Never mutate or
        # derail discovery when such a historical payload is encountered.
        return {"status": "LEGACY_AUDIENCE_MAP_NOT_MATERIALIZED"}
    matches = [
        opportunity
        for opportunity in mapping.opportunities
        if opportunity.platform == DistributionPlatform.TELEGRAM
        and _handle(opportunity.metadata, opportunity.url) == handle
    ]
    entity_id = int(observed.get("telegram_entity_id") or 0)
    if len(matches) != 1 or entity_id <= 0:
        return {"status": "AMBIGUOUS_OR_MISSING_OPPORTUNITY", "matches": len(matches)}
    old = matches[0]
    candidate_kind = getattr(candidate, "kind", None)
    if candidate_kind not in (OpportunityKind.CHANNEL, OpportunityKind.GROUP):
        return {"status": "INVALID_NATIVE_KIND"}
    previous_entity_id = int(old.metadata.get("telegram_entity_id") or 0)
    if previous_entity_id not in (0, entity_id):
        return {"status": "ENTITY_ID_MISMATCH"}

    timestamp = now or datetime.now(UTC)
    timestamp = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp.astimezone(UTC)
    latest = _message(observed)
    prior = dict(old.metadata)
    # Historic READY and post URLs must not survive a failed quality gate.
    new = {key: value for key, value in prior.items() if key not in _NATIVE_KEYS}
    new.update({key: observed[key] for key in _NATIVE_KEYS if key in observed})
    new["publisher_permission_verified"] = False
    new["research_latest_post_url"] = str(latest.get("url") or "") or None if latest else None
    new["research_latest_post_id"] = int(latest["message_id"]) if latest else None
    new["research_latest_post_published_at"] = (
        latest.get("published_at") if latest else None
    )
    new["research_latest_post_excerpt"] = (
        str(latest.get("text") or "")[:400] if latest else ""
    )
    new["research_last_native_checked_at"] = observed.get("source_checked_at")
    new["research_audience_status"] = (
        "DIRECT_CONFIRMED" if observed.get("community_fit_pass") is True
        else "REVIEW_REQUIRED"
    )
    can_act = _actionable_latest(observed, handle, candidate_kind, timestamp)
    if can_act:
        new["action_target_url"] = str(observed["action_target_url"])
        new["action_target_specific"] = (
            str(observed.get("action_target_mode") or "") != "STANDALONE_POST"
        )
        new["actionability"] = "NEEDS_PREFLIGHT"
        new["community_only_reason"] = None
    else:
        new["action_target_url"] = None
        new["action_target_specific"] = False
        new["actionability"] = "RESEARCH_ONLY"
        new["community_only_reason"] = (
            "AUDIENCE_FIT_UNCONFIRMED"
            if observed.get("community_fit_pass") is not True
            else "LATEST_NATIVE_TARGET_NOT_ELIGIBLE"
        )
    updated = old.model_copy(update={
        "metadata": new, "kind": candidate_kind,
        "title": str(getattr(candidate, "title", None) or old.title)[:300],
        "url": getattr(candidate, "url", None) or old.url,
    })
    # Validate nested content and the complete map before persistence.
    validated = mapping.model_copy(update={
        "opportunities": [
            updated if item.id == old.id else item for item in mapping.opportunities
        ],
    })
    AudienceDistributionMapView.model_validate(validated.model_dump(mode="json"))
    # Use the same service object as the customer preview to invalidate its
    # in-process map/opportunity caches as well as persist to the database.
    from app.audience_intelligence_service import (
        InMemoryAudienceIntelligenceService,
        audience_intelligence_service,
    )

    service = (
        audience_intelligence_service
        if audience_intelligence_service._store is store
        else InMemoryAudienceIntelligenceService(store)
    )
    service.update_opportunity(updated)
    return {
        "status": "SAVED_TARGET_SYNCED",
        "handle": handle,
        "opportunity_id": str(old.id),
        "native_entity_id": entity_id,
        "research_latest_post_url": new["research_latest_post_url"],
        "action_target_url": new["action_target_url"],
        "actionability": new["actionability"],
        "audience_status": new["research_audience_status"],
        "publisher_permission_verified": False,
    }
