"""Exact user-reviewed audiences are durable, not a paid semantic rerun."""

from uuid import uuid4

import pytest

from app.femdom_operator_telegram_review import PRODUCT, reconcile_operator_review
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TelegramCommunityRegistry


def _fixture():
    store = MemoryRuntimeStateStore()
    registry = TelegramCommunityRegistry(store)
    already_confirmed = {
        "findom_gospoja0", "poiskrabovirabin", "fem_tut",
        "moscowellebdsm1", "bizarre_afisha",
    }
    all_handles = [
        "findom_gospoja0", "poiskrabovirabin", "fem_tut",
        "moscowellebdsm1", "bizarre_afisha", "play_hard_community",
        "a_sfera", "bizarreclubmsk", "unrelated_crypto",
    ]
    for handle in all_handles:
        registry.upsert(
            product_id=PRODUCT,
            canonical_key=f"telegram:{handle}:community",
            title=handle,
            url=f"https://t.me/{handle}",
            kind="CHANNEL",
            metadata={
                "handle": handle,
                "community_fit_pass": handle in already_confirmed,
                "community_fit_reason": (
                    "PREVIOUSLY_CONFIRMED" if handle in already_confirmed else ""
                ),
                "native_research_status": "VERIFIED",
                "actionability": "RESEARCH_ONLY",
                "publisher_permission_verified": False,
            },
        )
    return store, registry


def test_manual_review_promotes_three_and_preserves_no_publish():
    store, registry = _fixture()
    initial = store.list_namespace("telegram_community_registry")
    preview = reconcile_operator_review(store, product_id=PRODUCT)
    assert preview["confirmed_before"] == 5
    assert preview["confirmed_after"] == 5
    assert preview["mode"] == "PREVIEW_ONLY"
    assert store.list_namespace("telegram_community_registry") == initial

    done = reconcile_operator_review(store, product_id=PRODUCT, commit=True)
    assert done["registry_before"] == 9
    assert done["registry_after"] == 9
    assert done["confirmed_after"] == 8
    assert done["added_confirmations"] == 3
    assert done["telegram_join_calls"] == 0
    assert done["telegram_send_calls"] == 0
    assert done["publisher_permission_granted"] is False
    rows = {
        registry.hydrate_metadata(row)["handle"]: row
        for row in registry.list_for_product(PRODUCT)
    }
    assert rows["unrelated_crypto"]["audience_fit"]["confirmed"] is False
    for handle in ("play_hard_community", "a_sfera", "bizarreclubmsk"):
        row = rows[handle]
        assert row["audience_fit"]["confirmed"] is True
        assert row["audience_fit"]["reason"] == "USER_MANUALLY_CONFIRMED"
        assert row["metadata"]["publisher_permission_verified"] is False

    for handle in ("findom_gospoja0", "poiskrabovirabin"):
        detail = registry.hydrate_metadata(rows[handle])["operator_review"]
        assert detail["observed_surface"] == "GROUP_STANDALONE"
        assert detail["account_join_needed"] == "YES_FOR_CONNECTED_ACCOUNT"
        assert detail["account_write_permission_verified"] is False

    linked = registry.hydrate_metadata(rows["bizarre_afisha"])["operator_review"]
    assert linked["related_post"] == "https://t.me/bizarreclubmsk/7552"
    assert linked["observed_surface"] == "NO_WRITE_AT_THIS_POST"

    repeated = reconcile_operator_review(store, product_id=PRODUCT, commit=True)
    assert repeated["added_confirmations"] == 0
    assert all(x["status"] == "ALREADY_APPLIED" for x in repeated["rows"])


def test_operator_review_is_strictly_product_scoped():
    store, _ = _fixture()
    with pytest.raises(ValueError, match="Restricted"):
        reconcile_operator_review(store, product_id=uuid4(), commit=True)


def test_operator_review_does_not_manufacture_missing_peers():
    store, registry = _fixture()
    custom = {
        "product_id": str(PRODUCT),
        "reviewed_on": "2026-10-25",
        "entries": [{
            "handle": "new_unsupported_peer",
            "post": "https://t.me/new_unsupported_peer/1",
            "audience": "DIRECT",
            "surface": "USER_OBSERVED_WRITE_SURFACE",
        }],
    }
    report = reconcile_operator_review(
        store, product_id=PRODUCT, commit=True, payload=custom,
    )
    assert report["rows"][0]["status"] == "MISSING_REGISTRY"
    assert len(registry.list_for_product(PRODUCT)) == 9
