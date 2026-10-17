from uuid import uuid4

from app.community_first_telegram_adapter import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE
from app.discovery_negative_memory_repair import (
    commit_discovery_negative_memory_repair,
    plan_discovery_negative_memory_repair,
)
from app.discovery_relevance_learning import DISCOVERY_NEGATIVE_LEARNING_NAMESPACE
from app.runtime_store import MemoryRuntimeStateStore


def _verified_registry_row(product_id, *, canonical_key="telegram:123:community") -> dict:
    return {
        "id": f"{product_id}:entity:123",
        "product_id": str(product_id),
        "canonical_key": canonical_key,
        "title": "Relevant community",
        "url": "https://t.me/relevant_handle",
        "kind": "CHANNEL",
        "metadata": {
            "community_fit_pass": True,
            "native_research_status": "VERIFIED",
            "source_type": "CHANNEL",
            "telegram_entity_id": 123,
            "handle": "relevant_handle",
        },
    }


def test_repair_plan_is_read_only_and_matches_entity_handle_aliases() -> None:
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
        "registry-row",
        _verified_registry_row(product_id),
    )
    original = {
        "product_id": str(product_id),
        "blocked_keys": {
            "telegram|telegram:123:community": {"count": 3},
            "telegram|telegram:relevant_handle:community": {"count": 2},
            "telegram|telegram:other:community": {"count": 4},
        },
        "negative_terms": {"running": {"count": 9}},
    }
    store.put(DISCOVERY_NEGATIVE_LEARNING_NAMESPACE, str(product_id), original)

    result = plan_discovery_negative_memory_repair(product_id, store=store)

    assert result["status"] == "REPAIR_PLANNED"
    assert result["removable_blocked_keys"] == [
        "telegram|telegram:123:community",
        "telegram|telegram:relevant_handle:community",
    ]
    assert store.get(DISCOVERY_NEGATIVE_LEARNING_NAMESPACE, str(product_id)) == original


def test_commit_removes_only_safe_exact_semantic_blocks() -> None:
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
        "verified",
        _verified_registry_row(product_id),
    )
    store.put(
        TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE,
        "invite",
        {
            "id": f"{product_id}:invite:abc",
            "product_id": str(product_id),
            "canonical_key": "telegram:invite:abc:community",
            "title": "Invite only",
            "url": "https://t.me/+InviteToken",
            "kind": "GROUP",
            "metadata": {
                "community_fit_pass": True,
                "native_research_status": "INVITE_ONLY",
                "source_type": "INVITE_COMMUNITY",
            },
        },
    )
    negative_terms = {"running": {"count": 9, "last_seen_at": "2026-10-17T00:00:00+00:00"}}
    store.put(
        DISCOVERY_NEGATIVE_LEARNING_NAMESPACE,
        str(product_id),
        {
            "product_id": str(product_id),
            "blocked_keys": {
                "telegram|telegram:123:community": {"count": 3},
                "telegram|telegram:invite:abc:community": {"count": 3},
                "telegram|telegram:other:community": {"count": 3},
            },
            "negative_terms": negative_terms,
        },
    )
    access_namespace = "telegram_community_restrictions"
    access_row = {"id": "restriction-1", "status": "NO_DISCUSSION"}
    store.put(access_namespace, "restriction-1", access_row)

    result = commit_discovery_negative_memory_repair(product_id, store=store)

    assert result["status"] == "REPAIR_COMMITTED"
    payload = store.get(DISCOVERY_NEGATIVE_LEARNING_NAMESPACE, str(product_id))
    assert payload["blocked_keys"] == {
        "telegram|telegram:invite:abc:community": {"count": 3},
        "telegram|telegram:other:community": {"count": 3},
    }
    assert payload["negative_terms"] == negative_terms
    assert store.get(access_namespace, "restriction-1") == access_row
    assert result["negative_terms_untouched"] is True
    assert result["access_restrictions_untouched"] is True


def test_non_fit_verified_registry_is_not_repaired() -> None:
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    row = _verified_registry_row(product_id)
    row["metadata"]["community_fit_pass"] = False
    store.put(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE, "registry-row", row)
    store.put(
        DISCOVERY_NEGATIVE_LEARNING_NAMESPACE,
        str(product_id),
        {
            "product_id": str(product_id),
            "blocked_keys": {"telegram|telegram:123:community": {"count": 3}},
            "negative_terms": {},
        },
    )

    result = commit_discovery_negative_memory_repair(product_id, store=store)

    assert result["status"] == "REPAIR_NOOP"
    assert result["committed"] is False
    payload = store.get(DISCOVERY_NEGATIVE_LEARNING_NAMESPACE, str(product_id))
    assert "telegram|telegram:123:community" in payload["blocked_keys"]
