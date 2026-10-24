"""Native Telegram latest-post targets must propagate without auto-approving audiences."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.audience_intelligence_service import InMemoryAudienceIntelligenceService
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_saved_target_bridge import sync_verified_telegram_opportunity


def _seed(store, *, handle="a_sfera", duplicates=False):
    pid = uuid4()
    opps = []
    for _ in range(2 if duplicates else 1):
        opps.append(DistributionOpportunityView(
            id=uuid4(), icp_id=uuid4(),
            platform=DistributionPlatform.TELEGRAM, kind=OpportunityKind.CHANNEL,
            canonical_key=f"telegram:{handle}:community",
            title="Historical Telegram candidate",
            url=f"https://t.me/{handle}",
            metadata={
                "handle": handle,
                "telegram_entity_id": 1642699396,
                "action_target_specific": True,
                "action_target_url": f"https://t.me/{handle}/2112",
                "native_research_status": "VERIFIED",
                "actionability": "READY",
            },
        ))
    store.put("audience_distribution_map", str(pid), AudienceDistributionMapView(
        product_id=pid, top_icp_count=1, opportunity_count=len(opps),
        opportunities=opps,
    ).model_dump(mode="json"))
    return pid, opps


def _candidate(now, *, fit=True, latest=2116, published=None, native_checked=None):
    published = published or now - timedelta(minutes=5)
    native_checked = native_checked or now
    return SimpleNamespace(
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.CHANNEL, title="Атмосфера", url="https://t.me/a_sfera",
        metadata={
            "handle": "a_sfera", "telegram_entity_id": 1642699396,
            "native_research_status": "VERIFIED",
            "registry_refresh_attempted": True,
            "registry_refresh_status": "VERIFIED",
            "community_fit_pass": fit,
            "community_fit_reason": "SEMANTIC_DIRECT_FIT" if fit else "SEMANTIC_ADJACENT",
            "source_checked_at": native_checked.isoformat(),
            "surface_capabilities": {
                "comment": "AVAILABLE", "reply": "UNKNOWN",
                "publisher_permission_verified": False,
            },
            "action_target_mode": "CHANNEL_COMMENT",
            "action_target_specific": True,
            "action_target_url": f"https://t.me/a_sfera/{latest}",
            "actionability": "NEEDS_PREFLIGHT",
            "recent_context": [{
                "message_id": latest,
                "text": "Тематическая встреча, обсуждаем организацию",
                "url": f"https://t.me/a_sfera/{latest}",
                "published_at": published.isoformat(),
            }, {
                "message_id": latest-1, "text": "Older",
                "url": f"https://t.me/a_sfera/{latest-1}",
                "published_at": (published-timedelta(days=1)).isoformat(),
            }],
        },
    )


def _saved(store, product_id):
    service = InMemoryAudienceIntelligenceService(store)
    return service.get(product_id).opportunities[0]


def test_current_direct_fit_preserves_stable_id_and_exact_newest_post():
    store = MemoryRuntimeStateStore()
    pid, old = _seed(store)
    now = datetime.now(UTC)
    result = sync_verified_telegram_opportunity(
        store, pid, _candidate(now), now=now,
    )
    assert result["status"] == "SAVED_TARGET_SYNCED"
    assert result["action_target_url"] == "https://t.me/a_sfera/2116"
    assert result["publisher_permission_verified"] is False
    saved = _saved(store, pid)
    assert saved.id == old[0].id
    assert saved.metadata["action_target_specific"] is True
    assert saved.metadata["actionability"] == "NEEDS_PREFLIGHT"
    assert saved.metadata["research_latest_post_url"] == "https://t.me/a_sfera/2116"
    assert saved.metadata["publisher_permission_verified"] is False
    assert store.get("audience_distribution_opportunity", str(saved.id)) is not None


def test_adjacent_fit_preserves_native_research_url_but_cannot_prepare_target():
    store = MemoryRuntimeStateStore()
    pid, original = _seed(store)
    now = datetime.now(UTC)
    report = sync_verified_telegram_opportunity(
        store, pid, _candidate(now, fit=False), now=now,
    )
    saved = _saved(store, pid)
    assert saved.id == original[0].id
    assert report["actionability"] == "RESEARCH_ONLY"
    assert saved.metadata["research_latest_post_url"] == "https://t.me/a_sfera/2116"
    assert saved.metadata["research_latest_post_id"] == 2116
    assert saved.metadata["research_audience_status"] == "REVIEW_REQUIRED"
    assert saved.metadata["action_target_url"] is None
    assert saved.metadata["action_target_specific"] is False


def test_expired_or_uncommentable_post_does_not_retain_legacy_action_target():
    now = datetime.now(UTC)
    for candidate in [
        _candidate(now, published=now - timedelta(days=25)),
        _candidate(now, native_checked=now - timedelta(hours=2)),
    ]:
        store = MemoryRuntimeStateStore()
        pid, _ = _seed(store)
        report = sync_verified_telegram_opportunity(store, pid, candidate, now=now)
        assert report["action_target_url"] is None
        saved = _saved(store, pid)
        assert saved.metadata["actionability"] == "RESEARCH_ONLY"
        assert saved.metadata["action_target_specific"] is False
    store = MemoryRuntimeStateStore()
    pid, _ = _seed(store)
    candidate = _candidate(now)
    candidate.metadata["surface_capabilities"]["comment"] = "UNAVAILABLE"
    report = sync_verified_telegram_opportunity(store, pid, candidate, now=now)
    assert report["action_target_url"] is None


def test_unverified_and_duplicate_opportunities_are_never_mutated():
    store = MemoryRuntimeStateStore()
    pid, _ = _seed(store, duplicates=True)
    before = deepcopy(store.get("audience_distribution_map", str(pid)))
    result = sync_verified_telegram_opportunity(
        store, pid, _candidate(datetime.now(UTC)), now=datetime.now(UTC),
    )
    assert result["status"] == "AMBIGUOUS_OR_MISSING_OPPORTUNITY"
    assert store.get("audience_distribution_map", str(pid)) == before
    store = MemoryRuntimeStateStore()
    pid, _ = _seed(store)
    before = deepcopy(store.get("audience_distribution_map", str(pid)))
    candidate = _candidate(datetime.now(UTC))
    candidate.metadata["registry_refresh_status"] = "RETRYABLE_FAILED"
    result = sync_verified_telegram_opportunity(store, pid, candidate)
    assert result["status"] == "NO_FRESH_NATIVE_INSPECTION"
    assert store.get("audience_distribution_map", str(pid)) == before


def test_latest_message_mismatch_cannot_promote_older_post():
    store = MemoryRuntimeStateStore()
    pid, _ = _seed(store)
    now = datetime.now(UTC)
    candidate = _candidate(now)
    candidate.metadata["action_target_url"] = "https://t.me/a_sfera/2115"
    result = sync_verified_telegram_opportunity(store, pid, candidate, now=now)
    assert result["action_target_url"] is None
    assert result["research_latest_post_url"] == "https://t.me/a_sfera/2116"



def test_verified_group_can_target_community_without_reply_to_last_message():
    now = datetime.now(UTC)
    store = MemoryRuntimeStateStore()
    pid, old = _seed(store, handle="a_sfera")
    candidate = _candidate(now)
    candidate.kind = OpportunityKind.GROUP
    candidate.metadata.update({
        "action_target_mode": "STANDALONE_POST",
        "action_target_specific": False,
        "action_target_url": "https://t.me/a_sfera",
        "surface_capabilities": {
            "comment": "UNKNOWN", "reply": "AVAILABLE",
            "standalone_post": "AVAILABLE",
            "publisher_permission_verified": False,
        },
    })
    result = sync_verified_telegram_opportunity(store, pid, candidate, now=now)
    assert result["status"] == "SAVED_TARGET_SYNCED"
    assert result["action_target_url"] == "https://t.me/a_sfera"
    assert result["publisher_permission_verified"] is False
    saved = _saved(store, pid)
    assert saved.id == old[0].id
    assert saved.kind == OpportunityKind.GROUP
    assert saved.metadata["action_target_mode"] == "STANDALONE_POST"
    assert saved.metadata["action_target_specific"] is False
    assert saved.metadata["actionability"] == "NEEDS_PREFLIGHT"

    candidate.metadata["surface_capabilities"]["standalone_post"] = "UNAVAILABLE"
    blocked = sync_verified_telegram_opportunity(store, pid, candidate, now=now)
    assert blocked["action_target_url"] is None
    assert blocked["actionability"] == "RESEARCH_ONLY"
