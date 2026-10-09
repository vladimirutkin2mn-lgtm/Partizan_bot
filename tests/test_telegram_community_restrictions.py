from types import SimpleNamespace
from uuid import uuid4

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_restrictions import TelegramCommunityRestrictionMemory


def test_failed_banned_publish_blocks_same_community_for_project() -> None:
    store = MemoryRuntimeStateStore()
    memory = TelegramCommunityRestrictionMemory(store)
    project_id = uuid4()
    action_id = uuid4()
    receipt = SimpleNamespace(
        action_id=action_id,
        outcome=SimpleNamespace(value="FAILED"),
        metadata={
            "target_username": "a_sfera",
            "error_code": "ACCOUNT_BANNED_IN_COMMUNITY",
            "restriction_signal": "ACCOUNT_RESTRICTED",
        },
    )

    recorded = memory.observe_publish_receipt(project_id, receipt)

    assert recorded is not None
    blocked = memory.get(project_id, "@A_SFERA")
    assert blocked is not None
    assert blocked["reason"] == "ACCOUNT_BANNED_IN_COMMUNITY"
    assert blocked["source_action_id"] == str(action_id)


def test_non_restriction_failure_is_not_remembered() -> None:
    store = MemoryRuntimeStateStore()
    memory = TelegramCommunityRestrictionMemory(store)
    project_id = uuid4()
    receipt = SimpleNamespace(
        action_id=uuid4(),
        outcome=SimpleNamespace(value="FAILED"),
        metadata={
            "target_username": "example",
            "error_code": "PUBLISH_FAILED",
            "restriction_signal": None,
        },
    )

    assert memory.observe_publish_receipt(project_id, receipt) is None
    assert memory.get(project_id, "example") is None


def test_restrictions_are_project_scoped() -> None:
    store = MemoryRuntimeStateStore()
    memory = TelegramCommunityRestrictionMemory(store)
    project_a = uuid4()
    project_b = uuid4()

    memory.remember(
        project_a,
        "same_channel",
        reason="ACCOUNT_BANNED_IN_COMMUNITY",
    )

    assert memory.get(project_a, "same_channel") is not None
    assert memory.get(project_b, "same_channel") is None
