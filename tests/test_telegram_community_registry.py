from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import (
    TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
    TelegramCommunityRegistry,
)


def _verified_metadata(
    *,
    handle: str,
    target: str,
    checked_at: datetime,
    fit_reason: str = "NATIVE_COMMUNITY_OVERLAP",
) -> dict:
    return {
        "handle": handle,
        "telegram_entity_id": 42,
        "source_type": "CHANNEL",
        "surface_kind": "CHANNEL",
        "native_research_status": "VERIFIED",
        "source_checked_at": checked_at.isoformat(),
        "last_activity_at": checked_at.isoformat(),
        "action_target_url": target,
        "action_target_specific": True,
        "action_target_resolution": "LATEST_UNBLOCKED_COMMUNITY_TARGET",
        "action_target_mode": "CHANNEL_COMMENT",
        "actionability": "NEEDS_PREFLIGHT",
        "community_fit_pass": True,
        "community_fit_reason": fit_reason,
        "community_fit_terms": ["femdom", "bdsm"],
    }


def test_handle_row_is_compacted_into_verified_entity_identity() -> None:
    store = MemoryRuntimeStateStore()
    registry = TelegramCommunityRegistry(store)
    product_id = uuid4()
    old_key = f"{product_id}:handle:play_hard_community"
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
        old_key,
        {
            "id": old_key,
            "product_id": str(product_id),
            "canonical_key": "telegram:play_hard_community:community",
            "title": "Play.Hard",
            "url": "https://t.me/play_hard_community",
            "kind": "CHANNEL",
            "metadata": {
                "handle": "play_hard_community",
                "community_fit_pass": True,
                "community_fit_reason": "SOURCE_BOUND_COMMUNITY_OVERLAP",
                "community_fit_terms": ["femdom"],
            },
            "updated_at": "2026-10-01T00:00:00+00:00",
        },
    )
    checked_at = datetime(2026, 10, 17, 18, 0, tzinfo=UTC)

    saved = registry.upsert(
        product_id=product_id,
        canonical_key="telegram:42:community",
        title="Play.Hard FemDom community",
        url="https://t.me/play_hard_community",
        kind="CHANNEL",
        metadata=_verified_metadata(
            handle="play_hard_community",
            target="https://t.me/play_hard_community/147",
            checked_at=checked_at,
        ),
    )

    rows = registry.list_for_product(product_id)
    assert len(rows) == 1
    assert saved["id"] == f"{product_id}:entity:42"
    assert saved["canonical_key"] == "telegram:42:community"
    assert "entity:42" in saved["aliases"]
    assert "handle:play_hard_community" in saved["aliases"]
    assert saved["native_snapshot"]["action_target_url"].endswith("/147")


def test_entity_identity_survives_handle_change_and_keeps_both_aliases() -> None:
    store = MemoryRuntimeStateStore()
    registry = TelegramCommunityRegistry(store)
    product_id = uuid4()
    first_checked = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
    second_checked = first_checked + timedelta(days=1)

    registry.upsert(
        product_id=product_id,
        canonical_key="telegram:42:community",
        title="Old title",
        url="https://t.me/old_handle",
        kind="CHANNEL",
        metadata=_verified_metadata(
            handle="old_handle",
            target="https://t.me/old_handle/10",
            checked_at=first_checked,
        ),
    )
    saved = registry.upsert(
        product_id=product_id,
        canonical_key="telegram:42:community",
        title="New title",
        url="https://t.me/new_handle",
        kind="CHANNEL",
        metadata=_verified_metadata(
            handle="new_handle",
            target="https://t.me/new_handle/11",
            checked_at=second_checked,
        ),
    )

    rows = registry.list_for_product(product_id)
    assert len(rows) == 1
    assert saved["id"] == f"{product_id}:entity:42"
    assert "handle:old_handle" in saved["aliases"]
    assert "handle:new_handle" in saved["aliases"]
    assert saved["native_snapshot"]["handle"] == "new_handle"
    assert saved["native_snapshot"]["action_target_url"].endswith("/11")


def test_latest_native_snapshot_wins_without_erasing_original_fit_provenance() -> None:
    store = MemoryRuntimeStateStore()
    registry = TelegramCommunityRegistry(store)
    product_id = uuid4()
    old_checked = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    new_checked = old_checked + timedelta(days=2)

    first = registry.upsert(
        product_id=product_id,
        canonical_key="telegram:42:community",
        title="Play.Hard",
        url="https://t.me/play_hard_community",
        kind="CHANNEL",
        metadata={
            **_verified_metadata(
                handle="play_hard_community",
                target="https://t.me/play_hard_community/111",
                checked_at=old_checked,
                fit_reason="SOURCE_BOUND_COMMUNITY_OVERLAP",
            ),
            "relevance_score": 0.99,
        },
    )
    assert first["audience_fit"]["reason"] == "SOURCE_BOUND_COMMUNITY_OVERLAP"

    saved = registry.upsert(
        product_id=product_id,
        canonical_key="telegram:42:community",
        title="Play.Hard",
        url="https://t.me/play_hard_community",
        kind="CHANNEL",
        metadata=_verified_metadata(
            handle="play_hard_community",
            target="https://t.me/play_hard_community/147",
            checked_at=new_checked,
            fit_reason="REGISTRY_CONFIRMED",
        ),
    )

    assert saved["audience_fit"]["reason"] == "SOURCE_BOUND_COMMUNITY_OVERLAP"
    assert saved["audience_fit"]["terms"] == ["femdom", "bdsm"]
    assert saved["native_snapshot"]["action_target_url"].endswith("/147")
    hydrated = registry.hydrate_metadata(saved)
    assert hydrated["action_target_url"].endswith("/147")
    assert hydrated["community_fit_reason"] == "SOURCE_BOUND_COMMUNITY_OVERLAP"


def test_unverified_observation_cannot_replace_verified_native_snapshot() -> None:
    store = MemoryRuntimeStateStore()
    registry = TelegramCommunityRegistry(store)
    product_id = uuid4()
    checked = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

    registry.upsert(
        product_id=product_id,
        canonical_key="telegram:42:community",
        title="Play.Hard",
        url="https://t.me/play_hard_community",
        kind="CHANNEL",
        metadata=_verified_metadata(
            handle="play_hard_community",
            target="https://t.me/play_hard_community/147",
            checked_at=checked,
        ),
    )
    saved = registry.upsert(
        product_id=product_id,
        canonical_key="telegram:play_hard_community:community",
        title="Search result title",
        url="https://t.me/play_hard_community",
        kind="CHANNEL",
        metadata={
            "handle": "play_hard_community",
            "native_research_status": "NOT_CHECKED",
            "source_checked_at": (checked + timedelta(days=1)).isoformat(),
            "action_target_url": "https://t.me/play_hard_community/999",
            "community_fit_pass": True,
            "community_fit_reason": "REGISTRY_CONFIRMED",
        },
    )

    assert saved["id"] == f"{product_id}:entity:42"
    assert saved["native_snapshot"]["native_research_status"] == "VERIFIED"
    assert saved["native_snapshot"]["action_target_url"].endswith("/147")
