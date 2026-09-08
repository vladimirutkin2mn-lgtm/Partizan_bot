from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from app.channel_execution import ChannelCapability, PublisherMode
from app.config import get_settings
from app.customer_channels import customer_channel_service
from app.distribution_types import DistributionPlatform
from app.telegram_research import (
    ACTION_TARGET_FRESHNESS_SECONDS,
    TelegramResearchUnavailableError,
    get_telegram_research_connector,
)

EXPECTED_RELEASE_SHA = "20aa9433dc7301cf541359535eb22e386a265f7e"
RESEARCH_QUERY = "telegram community growth marketing"
KNOWN_PUBLIC_HANDLES = ["telegram"]


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _publish_readiness() -> tuple[bool, bool]:
    capabilities = customer_channel_service._capabilities(  # noqa: SLF001 - verification probe
        DistributionPlatform.TELEGRAM,
        execution_ready=False,
        execution_blocker="autonomous execution is not supported for this channel",
    )
    publish = next(
        item for item in capabilities if item.capability == ChannelCapability.PUBLISH
    )
    publisher_modes = customer_channel_service._publisher_mode_options(  # noqa: SLF001
        DistributionPlatform.TELEGRAM
    )
    client_owned = next(
        item for item in publisher_modes if item.mode == PublisherMode.CLIENT_OWNED
    )
    return publish.ready, client_owned.available


async def main() -> None:
    settings = get_settings()
    publish_ready, client_owned_available = _publish_readiness()

    config_status = {
        "release_sha": settings.partizan_release_sha,
        "telegram_research_provider": settings.telegram_research_provider,
        "telegram_research_public_ready": settings.telegram_research_public_ready,
        "api_id_configured": settings.telegram_research_api_id is not None,
        "api_hash_configured": settings.telegram_research_api_hash is not None,
        "session_configured": settings.telegram_research_session is not None,
        "telegram_publish_ready": publish_ready,
        "telegram_client_owned_publish_available": client_owned_available,
    }

    assert settings.partizan_release_sha == EXPECTED_RELEASE_SHA, config_status
    assert not publish_ready, config_status
    assert not client_owned_available, config_status

    connector = get_telegram_research_connector(settings)
    if connector is None:
        print(json.dumps({"status": "NOT_READY", **config_status}, sort_keys=True))
        raise SystemExit("Telegram production research is not enabled")

    try:
        snapshots = await connector.discover(
            query=RESEARCH_QUERY,
            known_handles=KNOWN_PUBLIC_HANDLES,
        )
    except TelegramResearchUnavailableError as exc:
        print(
            json.dumps(
                {
                    "status": "UNAVAILABLE",
                    **config_status,
                    "reason": str(exc),
                },
                sort_keys=True,
            )
        )
        raise SystemExit("Telegram production research transport is unavailable") from exc

    if not snapshots:
        print(json.dumps({"status": "NO_RESULTS", **config_status}, sort_keys=True))
        raise SystemExit("Telegram production research returned no public communities")

    preferred = next(
        (item for item in snapshots if item.username.lower() == "telegram"),
        snapshots[0],
    )

    context = [
        {
            "message_id": item.message_id,
            "published_at": _iso(item.published_at),
            "url": item.url,
            "matched_terms": list(item.matched_terms),
        }
        for item in preferred.recent_context[:8]
    ]

    if preferred.action_target_url is not None:
        target_context = next(
            (item for item in preferred.recent_context if item.url == preferred.action_target_url),
            None,
        )
        assert target_context is not None
        assert target_context.matched_terms
        assert target_context.published_at is not None
        checked_at = preferred.source_checked_at.astimezone(UTC)
        published_at = target_context.published_at.astimezone(UTC)
        age_seconds = (checked_at - published_at).total_seconds()
        assert 0 <= age_seconds <= ACTION_TARGET_FRESHNESS_SECONDS

    evidence = {
        "status": "VERIFIED",
        **config_status,
        "source_url": preferred.url,
        "telegram_entity_id": preferred.entity_id,
        "username": preferred.username,
        "title": preferred.title,
        "community_kind": preferred.kind.value,
        "source_checked_at": _iso(preferred.source_checked_at),
        "last_activity_at": _iso(preferred.last_activity_at),
        "member_count_present": preferred.member_count is not None,
        "comment_surface": preferred.comment_surface.value,
        "reply_surface": preferred.reply_surface.value,
        "standalone_post_surface": preferred.standalone_post_surface.value,
        "linked_discussion_id_present": preferred.linked_discussion_id is not None,
        "action_target_url": preferred.action_target_url,
        "recent_context": context,
        "recent_context_count": len(preferred.recent_context),
    }

    serialized = json.dumps(evidence, sort_keys=True)
    secret_values = [
        settings.telegram_research_api_hash.get_secret_value()
        if settings.telegram_research_api_hash is not None
        else "",
        settings.telegram_research_session.get_secret_value()
        if settings.telegram_research_session is not None
        else "",
    ]
    for secret in secret_values:
        if secret:
            assert secret not in serialized

    evidence["secrets_absent_from_evidence"] = True
    print(json.dumps(evidence, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
