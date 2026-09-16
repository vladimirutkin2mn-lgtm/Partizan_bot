from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from app.config import get_settings
from app.telegram_research import (
    TelegramResearchUnavailableError,
    get_telegram_research_connector,
)

EXPECTED_RELEASE_SHA = "4f3f0946fd3f651994176fdad166e19c7fb8564f"
RESEARCH_QUERY = "growth marketing community"
KNOWN_PUBLIC_HANDLES = ["telegram"]


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


async def main() -> None:
    settings = get_settings()
    config_status = {
        "release_sha": settings.partizan_release_sha,
        "telegram_research_provider": settings.telegram_research_provider,
        "telegram_research_public_ready": settings.telegram_research_public_ready,
        "api_id_configured": settings.telegram_research_api_id is not None,
        "api_hash_configured": settings.telegram_research_api_hash is not None,
        "session_configured": settings.telegram_research_session is not None,
    }

    assert settings.partizan_release_sha == EXPECTED_RELEASE_SHA, config_status

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

    evidence = {
        "status": "VERIFIED",
        **config_status,
        "source_url": preferred.url,
        "telegram_entity_id": preferred.entity_id,
        "username": preferred.username,
        "community_kind": preferred.kind.value,
        "source_checked_at": _iso(preferred.source_checked_at),
        "last_activity_at": _iso(preferred.last_activity_at),
        "member_count_present": preferred.member_count is not None,
        "recent_context_count": len(preferred.recent_context),
        "action_target_present": preferred.action_target_url is not None,
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
