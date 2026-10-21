"""Offline registry triage must preserve all publisher and source state."""

from copy import deepcopy
from uuid import UUID

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_registry_snapshot_review import snapshot_review

PRODUCT = UUID("52d7e59e-5ac5-4363-b829-c93e33e48272")
PROJECT = UUID("124cdd70-49b0-48b3-aee0-a037dda4bcd0")


def test_read_only_snapshot_keeps_last_post_without_promoting_rejected_audience():
    store = MemoryRuntimeStateStore()
    store.put("customer_acquisition_projects", str(PROJECT), {
        "product_id": str(PRODUCT), "channel_preferences": {"TELEGRAM": "DISABLED"}
    })
    registry = TelegramCommunityRegistry(store)
    registry.upsert(
        product_id=PRODUCT, canonical_key="telegram:a_sfera:community",
        title="ATMOSPHERE", url="https://t.me/a_sfera", kind="CHANNEL",
        metadata={
            "handle": "a_sfera", "about": "БДСМ встречи",
            "native_research_status": "VERIFIED",
            "community_fit_pass": False,
            "source_checked_at": "2026-10-21T09:00:00+00:00",
            "surface_capabilities": {"comment": "AVAILABLE"},
            "recent_context": [{
                "message_id": 2116, "text": "Встреча в Уфе",
                "url": "https://t.me/a_sfera/2116",
                "published_at": "2026-10-20T06:11:01+00:00",
            }],
            "action_target_specific": False, "action_target_url": None,
            "actionability": "RESEARCH_ONLY",
        },
    )
    before = deepcopy(store._payloads)
    result = snapshot_review(store, project_id=PROJECT, product_id=PRODUCT)
    assert result["registry_count"] == 1
    assert result["audience_confirmed_count"] == 0
    assert result["recent_context_available_count"] == 1
    assert result["exact_action_target_count"] == 0
    a = result["communities"][0]
    assert a["research_only_latest_post"]["url"] == "https://t.me/a_sfera/2116"
    assert a["comment_surface"] == "AVAILABLE"
    assert a["action_target_url"] is None
    assert a["publisher_permission_verified"] is False
    assert store._payloads == before
    assert store.get("customer_acquisition_projects", str(PROJECT))[
        "channel_preferences"
    ]["TELEGRAM"] == "DISABLED"
