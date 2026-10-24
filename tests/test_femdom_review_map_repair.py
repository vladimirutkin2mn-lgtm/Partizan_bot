"""A missing manually reviewed map row may be recovered, never made sendable."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_operator_telegram_review import PRODUCT
from app.femdom_review_map_repair import seed_missing_reviewed_opportunity
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_saved_target_bridge import sync_verified_telegram_opportunity


def _setup(*, stale=False, duplicate=False, handle="bizarreclubmsk"):
    store = MemoryRuntimeStateStore()
    now = datetime.now(UTC)
    icp = uuid4()
    old = DistributionOpportunityView(
        id=uuid4(), icp_id=icp, platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL, canonical_key="telegram:111:community",
        title="Previous approved research", url="https://t.me/a_sfera",
        metadata={"handle": "a_sfera", "telegram_entity_id": 111},
    )
    if duplicate:
        old = old.model_copy(update={
            "url": f"https://t.me/{handle}", "metadata": {"handle": handle}
        })
    opps = [old, old.model_copy(update={"id": uuid4()})] if duplicate else [old]
    store.put("audience_distribution_map", str(PRODUCT), AudienceDistributionMapView(
        product_id=PRODUCT, top_icp_count=1, opportunity_count=len(opps),
        opportunities=opps,
    ).model_dump(mode="json"))
    registry = TelegramCommunityRegistry(store)
    registry.upsert(
        product_id=PRODUCT, canonical_key="telegram:234567:community",
        title="Bizarre community", url=f"https://t.me/{handle}",
        kind="CHANNEL", metadata={
            "handle": handle, "telegram_entity_id": 234567,
            "native_research_status": "VERIFIED",
            "community_fit_pass": True,
            "community_fit_reason": "USER_MANUALLY_CONFIRMED",
        },
    )
    checked = now - timedelta(minutes=31 if stale else 1)
    candidate = SimpleNamespace(
        kind=OpportunityKind.CHANNEL, title="Bizarre community",
        url=f"https://t.me/{handle}", metadata={
            "handle": handle, "telegram_entity_id": 234567,
            "native_research_status": "VERIFIED",
            "source_checked_at": checked.isoformat(),
            "registry_refresh_attempted": True, "registry_refresh_status": "VERIFIED",
            "community_fit_pass": True,
            "action_target_mode": "CHANNEL_COMMENT",
            "action_target_specific": True,
            "action_target_url": f"https://t.me/{handle}/8627",
            "surface_capabilities": {"comment": "AVAILABLE"},
            "recent_context": [{
                "message_id": 8627, "text": "Тематическая встреча с открытыми комментариями.",
                "url": f"https://t.me/{handle}/8627",
                "published_at": checked.isoformat(),
            }],
        },
    )
    return store, icp, candidate, now


def test_missing_manual_peer_can_be_seeded_research_only_and_then_native_synced():
    store, icp, candidate, now = _setup()
    result = seed_missing_reviewed_opportunity(
        store, product_id=PRODUCT, icp_id=icp, candidate=candidate, now=now
    )
    assert result["status"] == "SEEDED_RESEARCH_ONLY"
    raw = store.get("audience_distribution_map", str(PRODUCT))
    assert raw["opportunity_count"] == 2
    peer = raw["opportunities"][-1]
    assert peer["metadata"]["action_target_url"] is None
    assert peer["metadata"]["actionability"] == "RESEARCH_ONLY"
    assert peer["metadata"]["publisher_permission_verified"] is False
    assert store.get("audience_distribution_opportunity", str(peer["id"]))
    second = seed_missing_reviewed_opportunity(
        store, product_id=PRODUCT, icp_id=icp, candidate=candidate, now=now
    )
    assert second["status"] == "ALREADY_PRESENT"
    assert store.get("audience_distribution_map", str(PRODUCT))["opportunity_count"] == 2
    native = sync_verified_telegram_opportunity(
        store, PRODUCT, candidate, now=now
    )
    assert native["status"] == "SAVED_TARGET_SYNCED"
    assert native["actionability"] == "NEEDS_PREFLIGHT"
    assert native["publisher_permission_verified"] is False


def test_duplicate_legacy_rows_are_not_deleted_or_chosen_without_identity():
    store, icp, candidate, now = _setup(duplicate=True)
    before = store.get("audience_distribution_map", str(PRODUCT))
    result = seed_missing_reviewed_opportunity(
        store, product_id=PRODUCT, icp_id=icp, candidate=candidate, now=now
    )
    assert result == {"status": "DUPLICATE_SAVED_OPPORTUNITIES", "matches": 2}
    assert store.get("audience_distribution_map", str(PRODUCT)) == before


def test_stale_native_evidence_or_unknown_community_cannot_seed():
    store, icp, candidate, now = _setup(stale=True)
    assert seed_missing_reviewed_opportunity(
        store, product_id=PRODUCT, icp_id=icp, candidate=candidate, now=now
    )["status"] == "FRESH_NATIVE_EVIDENCE_REQUIRED"
    store, icp, candidate, now = _setup(handle="unreviewed_chat")
    assert seed_missing_reviewed_opportunity(
        store, product_id=PRODUCT, icp_id=icp, candidate=candidate, now=now
    )["status"] == "NOT_MANUALLY_REVIEWED"
