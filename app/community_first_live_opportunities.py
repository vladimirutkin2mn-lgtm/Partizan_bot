from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.customer_live_opportunities import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerLiveOpportunityUpsert,
    customer_live_opportunity_service,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_participation_target import TelegramParticipationMode

RECHECK_INTERVAL = timedelta(days=7)


def sync_community_first_live_opportunities(
    product_id: UUID,
    opportunities: list[object],
    *,
    store: RuntimeStateStore | None = None,
) -> int:
    """Surface join-required communities and explicit participation actions."""
    runtime_store = store or get_runtime_store()
    project_ids = _project_ids(runtime_store, product_id)
    if not project_ids:
        return 0
    synced = 0
    now = datetime.now(UTC)
    for opportunity in opportunities:
        if getattr(opportunity, "platform", None) != DistributionPlatform.TELEGRAM:
            continue
        metadata = getattr(opportunity, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        checked_at = _parse_datetime(metadata.get("source_checked_at")) or now
        title = str(getattr(opportunity, "title", "") or "Telegram community")[:420]
        score = getattr(opportunity, "relevance_score", None)
        rationale = str(
            getattr(opportunity, "rationale", None)
            or "Partizan found target-audience overlap in this Telegram community."
        )[:4000]
        access_state = str(metadata.get("access_state") or "").upper()
        invite_url = str(metadata.get("invite_url") or "").strip()
        if access_state == "JOIN_REQUIRED" and invite_url:
            request = CustomerLiveOpportunityUpsert(
                source="RESEARCH",
                platform="TELEGRAM",
                surface="COMMUNITY",
                kind="CHANNEL",
                title=f"{title} — access required",
                url=invite_url,
                rationale=rationale,
                recommended_action=(
                    "Request access first. After access is granted, inspect the newest "
                    "participation surface before drafting any message."
                ),
                relevance_score=float(score) if score is not None else None,
                publishability="JOIN_REQUIRED",
                publishability_detail="Relevant community requires joining or approval first.",
                discovered_at=checked_at,
                expires_at=checked_at + RECHECK_INTERVAL,
                last_checked_at=checked_at,
                status="ACTIVE",
                metadata={"access_state": "JOIN_REQUIRED"},
            )
            synced += _upsert(project_ids, request)
            continue

        mode = str(metadata.get("action_target_mode") or "").upper()
        target_url = str(metadata.get("action_target_url") or "").strip()
        if not target_url or mode not in _LIVE_KIND_BY_MODE:
            continue
        target_specific = metadata.get("action_target_specific") is True
        if mode != TelegramParticipationMode.STANDALONE_POST.value and not target_specific:
            continue

        published_at = _target_published_at(metadata, target_url) if target_specific else None
        standalone = mode == TelegramParticipationMode.STANDALONE_POST.value
        request = CustomerLiveOpportunityUpsert(
            source="RESEARCH",
            platform="TELEGRAM",
            surface="COMMUNITY",
            kind=_LIVE_KIND_BY_MODE[mode],
            title=(
                f"{title} — community post surface"
                if standalone
                else f"{title} — latest participation surface"
            ),
            url=target_url,
            rationale=rationale,
            recommended_action=(
                "Preflight this Telegram community before drafting a standalone post."
                if standalone
                else "Preflight this latest unblocked Telegram surface before drafting content."
            ),
            relevance_score=float(score) if score is not None else None,
            publishability="NEEDS_REVIEW",
            publishability_detail=(
                "Community write access still requires account-specific preflight."
                if standalone
                else (
                    "Post age is context, not a hard rejection rule; membership and write access "
                    "still require preflight."
                )
            ),
            discovered_at=checked_at,
            expires_at=checked_at + RECHECK_INTERVAL,
            last_checked_at=checked_at,
            status="ACTIVE",
            metadata={
                "telegram_entity_id": metadata.get("telegram_entity_id"),
                "handle": metadata.get("handle"),
                "linked_discussion_id": metadata.get("linked_discussion_id"),
                "source_published_at": published_at.isoformat() if published_at else None,
                "target_age_days": metadata.get("target_age_days"),
                "action_target_mode": mode,
                "action_target_specific": target_specific,
            },
        )
        synced += _upsert(project_ids, request)
    return synced


_LIVE_KIND_BY_MODE = {
    TelegramParticipationMode.CHANNEL_COMMENT.value: "COMMENT",
    TelegramParticipationMode.GROUP_REPLY.value: "REPLY",
    TelegramParticipationMode.STANDALONE_POST.value: "STANDALONE_POST",
}


def _project_ids(store: RuntimeStateStore, product_id: UUID) -> list[UUID]:
    result: list[UUID] = []
    for row in store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
        if row.get("deleted_at") or str(row.get("product_id") or "") != str(product_id):
            continue
        try:
            result.append(UUID(str(row["id"])))
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _upsert(project_ids: list[UUID], request: CustomerLiveOpportunityUpsert) -> int:
    for project_id in project_ids:
        customer_live_opportunity_service.upsert(project_id, request)
    return len(project_ids)


def _target_published_at(metadata: dict, target_url: str) -> datetime | None:
    target = _canonical_url(target_url)
    for item in list(metadata.get("recent_context") or []):
        if isinstance(item, dict) and _canonical_url(item.get("url")) == target:
            return _parse_datetime(item.get("published_at"))
    return None


def _canonical_url(value: object) -> str:
    return str(value or "").strip().split("?", 1)[0].rstrip("/")


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
