from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramSurfaceKind,
)


class TelegramParticipationMode(StrEnum):
    CHANNEL_COMMENT = "CHANNEL_COMMENT"
    GROUP_REPLY = "GROUP_REPLY"
    STANDALONE_POST = "STANDALONE_POST"


@dataclass(frozen=True, slots=True)
class TelegramParticipationTarget:
    mode: TelegramParticipationMode | None
    target_url: str | None
    target_specific: bool
    resolution: str
    actionability: str
    reason: str | None = None
    target_published_at: datetime | None = None

    def metadata(self, *, now: datetime | None = None) -> dict:
        checked_now = now or datetime.now(UTC)
        published_at = self.target_published_at
        return {
            "action_target_url": self.target_url,
            "action_target_specific": self.target_specific,
            "action_target_resolution": self.resolution,
            "action_target_mode": self.mode.value if self.mode is not None else None,
            "action_target_quality": (
                "LATEST_COMMUNITY_SURFACE"
                if self.target_specific
                else "COMMUNITY_WRITE_SURFACE"
                if self.mode == TelegramParticipationMode.STANDALONE_POST
                else None
            ),
            "actionability": self.actionability,
            "community_only_reason": self.reason,
            "target_published_at": (
                published_at.isoformat() if published_at is not None else None
            ),
            "target_age_days": (
                None
                if published_at is None
                else round(
                    max(0.0, (checked_now - _aware(published_at)).total_seconds()) / 86400,
                    2,
                )
            ),
        }


def select_telegram_participation_target(
    snapshot: TelegramCommunitySnapshot,
    *,
    blocked_urls: set[str] | None = None,
) -> TelegramParticipationTarget:
    """Choose the actual newest native message; never rescue an older message."""
    blocked = {_normalize_url(value) for value in (blocked_urls or set()) if value}
    # Telegram message IDs are monotonic within one peer. A post without a
    # publication date (e.g. a media post) is still the newest if its ID is highest.
    latest = max(snapshot.recent_context, key=lambda item: item.message_id, default=None)
    latest_url = str(latest.url or "").strip() if latest is not None else ""
    latest_blocked = bool(latest_url and _normalize_url(latest_url) in blocked)
    latest_usable = bool(latest_url and not latest_blocked)
    unavailable_reason = (
        "NO_RECENT_POST"
        if latest is None
        else "LATEST_POST_URL_UNKNOWN"
        if not latest_url
        else "LATEST_POST_BLOCKED"
        if latest_blocked
        else None
    )

    if snapshot.kind == TelegramSurfaceKind.CHANNEL:
        if snapshot.comment_surface != SurfaceAvailability.AVAILABLE:
            return TelegramParticipationTarget(
                mode=None,
                target_url=None,
                target_specific=False,
                resolution="COMMUNITY_ONLY",
                actionability="RESEARCH_ONLY",
                reason="NO_COMMENT_SURFACE",
            )
        if not latest_usable:
            return TelegramParticipationTarget(
                mode=None,
                target_url=None,
                target_specific=False,
                resolution="COMMUNITY_ONLY",
                actionability="RESEARCH_ONLY",
                reason=unavailable_reason,
            )
        return TelegramParticipationTarget(
            mode=TelegramParticipationMode.CHANNEL_COMMENT,
            target_url=latest_url,
            target_specific=True,
            resolution="LATEST_COMMUNITY_TARGET",
            actionability="NEEDS_PREFLIGHT",
            target_published_at=_aware(latest.published_at),
        )

    if snapshot.kind == TelegramSurfaceKind.GROUP:
        if snapshot.reply_surface == SurfaceAvailability.AVAILABLE and latest_usable:
            return TelegramParticipationTarget(
                mode=TelegramParticipationMode.GROUP_REPLY,
                target_url=latest_url,
                target_specific=True,
                resolution="LATEST_COMMUNITY_TARGET",
                actionability="NEEDS_PREFLIGHT",
                target_published_at=_aware(latest.published_at),
            )
        # Standalone posting is a different action against the group itself,
        # not a reply to an older message.
        if snapshot.standalone_post_surface == SurfaceAvailability.AVAILABLE:
            return TelegramParticipationTarget(
                mode=TelegramParticipationMode.STANDALONE_POST,
                target_url=snapshot.url,
                target_specific=False,
                resolution="COMMUNITY_STANDALONE_POST",
                actionability="NEEDS_PREFLIGHT",
            )
        return TelegramParticipationTarget(
            mode=None,
            target_url=None,
            target_specific=False,
            resolution="COMMUNITY_ONLY",
            actionability="RESEARCH_ONLY",
            reason=(
                unavailable_reason
                if snapshot.reply_surface == SurfaceAvailability.AVAILABLE
                else "NO_GROUP_WRITE_SURFACE"
            ),
        )

    return TelegramParticipationTarget(
        mode=None,
        target_url=None,
        target_specific=False,
        resolution="COMMUNITY_ONLY",
        actionability="RESEARCH_ONLY",
        reason="UNSUPPORTED_TELEGRAM_ENTITY",
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _normalize_url(value: str) -> str:
    return str(value or "").strip().rstrip("/").split("?", 1)[0]
