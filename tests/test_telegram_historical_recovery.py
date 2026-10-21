"""Historical Telegram recovery does not turn previous research into authorization."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.audience_intelligence_service import InMemoryAudienceIntelligenceService
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_historical_recovery import (
    _native_target_valid,
    _recover_opportunity,
    recover,
)
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramKnownCommunityInspection,
    TelegramRecentContext,
    TelegramSurfaceKind,
)


def _legacy(store, project_id, product_id, handles):
    records = [
        DistributionOpportunityView(
            id=uuid4(),
            icp_id=uuid4(),
            platform=DistributionPlatform.TELEGRAM,
            kind=OpportunityKind.CHANNEL,
            canonical_key=f"legacy:{handle}",
            title=handle,
            url=f"https://t.me/{handle}",
            relevance_score=80,
            metadata={
                "handle": handle,
                "native_research_status": "VERIFIED",
                "community_fit_pass": True,
                "actionability": "READY",
                "action_target_url": f"https://t.me/{handle}/88",
                "action_target_specific": True,
            },
        )
        for handle in handles
    ]
    store.put(
        "audience_distribution_map", str(product_id),
        AudienceDistributionMapView(
            product_id=product_id,
            top_icp_count=1,
            opportunity_count=len(records),
            opportunities=records,
        ).model_dump(mode="json"),
    )
    store.put("customer_acquisition_projects", str(project_id), {
        "id": str(project_id), "product_id": str(product_id),
        "channel_preferences": {"TELEGRAM": "RESEARCH_ONLY"},
        "research_state": "READY",
    })
    store.put("growth_mandate", str(product_id), {
        "product_id": str(product_id), "status": "PAUSED",
        "allowed_platforms": [],
    })
    return records


class NativeOnly:
    _known_handle_limit = 3

    def __init__(self, now):
        self.now = now
        self.reads = []

    async def inspect_known(self, handles):
        self.reads.append(tuple(handles))
        return [
            TelegramKnownCommunityInspection(
                handle=handle,
                status="VERIFIED",
                snapshot=TelegramCommunitySnapshot(
                    entity_id=800 + index,
                    username=handle,
                    title=f"FemDom BDSM chat {handle}",
                    kind=TelegramSurfaceKind.CHANNEL,
                    url=f"https://t.me/{handle}",
                    about="FemDom BDSM community conversations",
                    comment_surface=SurfaceAvailability.AVAILABLE,
                    source_checked_at=self.now,
                    recent_context=(
                        TelegramRecentContext(
                            message_id=200,
                            text="FemDom BDSM community discussion and boundaries",
                            published_at=self.now - timedelta(minutes=5),
                            url=f"https://t.me/{handle}/200",
                        ),
                        TelegramRecentContext(
                            message_id=199, text="Older message",
                            published_at=self.now - timedelta(days=1),
                            url=f"https://t.me/{handle}/199",
                        ),
                    ),
                ),
            )
            for index, handle in enumerate(handles)
        ]

    async def inspect_latest_known(self, handles):
        raise AssertionError("Freshly staged unverified communities need fit evidence")

    async def discover(self, **kwargs):
        raise AssertionError("Must not run Telegram global search")


@pytest.mark.asyncio
async def test_paused_mandate_still_recovers_saved_public_peers_without_new_search():
    store = MemoryRuntimeStateStore()
    project_id, product_id = uuid4(), uuid4()
    handles = ["a_sfera", "play_hard_community", "bizarre_afisha"]
    old = _legacy(store, project_id, product_id, handles)
    now = datetime.now(UTC)
    native = NativeOnly(now)
    product = SimpleNamespace(
        id=product_id, name="FemDom", market="online",
        language="English", description="FemDom BDSM",
        problem_or_desire="FemDom connections", value_proposition="BDSM community",
        known_audience=["FemDom BDSM"],
    )
    icp = SimpleNamespace(
        id=uuid4(), rank=1, title="FemDom BDSM audience",
        pain="Find BDSM discussions", trigger="Meet community",
        description="FemDom communities", alternatives=[],
    )
    result = await recover(
        store=store, project_id=project_id, product_id=product_id,
        product=product, icps=[icp], connector=native,
    )
    assert result["registry_before"] == 0
    assert result["registry_after"] == 3
    assert result["native_checked"] == 3
    assert result["native_verified"] == 3
    assert result["audience_fit_confirmed"] == 3
    assert result["specific_targets_retained"] == 3
    assert len(native.reads) == 1
    assert set(native.reads[0]) == set(handles)
    assert all(value == 0 for value in result["effects"].values())
    assert len(TelegramCommunityRegistry(store).list_for_product(product_id)) == 3
    saved = InMemoryAudienceIntelligenceService(store).get(product_id)
    assert {item.id for item in saved.opportunities} == {item.id for item in old}
    assert all(item.metadata["action_target_url"].endswith("/200") for item in saved.opportunities)
    assert all(item.metadata["actionability"] == "NEEDS_PREFLIGHT" for item in saved.opportunities)
    assert all(item.metadata["surface_capabilities"]["publisher_permission_verified"] is False
               for item in saved.opportunities)
    assert store.get("growth_mandate", str(product_id))["status"] == "PAUSED"
    assert store.get("customer_acquisition_projects", str(project_id))[
        "channel_preferences"
    ]["TELEGRAM"] == "RESEARCH_ONLY"


def test_latest_target_requires_exact_message_readable_context_and_fresh_native_check():
    now = datetime.now(UTC)
    base = {
        "native_research_status": "VERIFIED",
        "community_fit_pass": True,
        "action_target_specific": True,
        "action_target_mode": "CHANNEL_COMMENT",
        "action_target_url": "https://t.me/a_sfera/200",
        "source_checked_at": now.isoformat(),
        "recent_context": [{
            "message_id": 200, "url": "https://t.me/a_sfera/200",
            "text": "FemDom community discussion",
            "published_at": now.isoformat(),
        }],
    }
    assert _native_target_valid(base, "a_sfera", now=now)
    assert not _native_target_valid(
        {**base, "recent_context": [{**base["recent_context"][0], "text": ""}]},
        "a_sfera", now=now,
    )
    assert not _native_target_valid(
        {**base, "action_target_url": "https://t.me/a_sfera/199"},
        "a_sfera", now=now,
    )
    assert not _native_target_valid(
        {**base, "community_fit_pass": False}, "a_sfera", now=now
    )
    assert not _native_target_valid(
        {**base, "source_checked_at": (now - timedelta(days=1)).isoformat()},
        "a_sfera", now=now,
    )


def test_unrelated_or_stale_native_candidate_cannot_inherit_old_target():
    store = MemoryRuntimeStateStore()
    project_id, product_id = uuid4(), uuid4()
    original = _legacy(store, project_id, product_id, ["a_sfera"])[0]
    candidate = SimpleNamespace(
        kind=OpportunityKind.CHANNEL,
        title="A Sfera",
        url="https://t.me/a_sfera",
        metadata={
            "handle": "a_sfera", "registry_refresh_attempted": True,
            "registry_refresh_status": "VERIFIED", "community_fit_pass": False,
            "native_research_status": "VERIFIED",
            "action_target_url": "https://t.me/a_sfera/200",
            "action_target_specific": True,
        },
    )
    result = _recover_opportunity(original, candidate, now=datetime.now(UTC))
    assert result.id == original.id
    assert result.metadata["action_target_url"] is None
    assert result.metadata["actionability"] == "RESEARCH_ONLY"
    assert result.metadata["community_fit_pass"] is False


@pytest.mark.asyncio
async def test_missing_connector_stages_history_but_never_copies_stale_ready_target():
    store = MemoryRuntimeStateStore()
    project_id, product_id = uuid4(), uuid4()
    _legacy(store, project_id, product_id, ["a_sfera"])
    result = await recover(
        store=store, project_id=project_id, product_id=product_id,
        product=SimpleNamespace(id=product_id), icps=[], connector=None,
    )
    assert result["mode"] == "NATIVE_CONNECTOR_OR_ICP_UNAVAILABLE"
    assert result["registry_after"] == 1
    assert result["native_checked"] == 0
    item = InMemoryAudienceIntelligenceService(store).get(product_id).opportunities[0]
    assert item.metadata["action_target_url"] is None
    assert item.metadata["native_research_status"] == "UNVERIFIED"
    assert item.metadata["actionability"] == "RESEARCH_ONLY"


@pytest.mark.asyncio
async def test_oneoff_respects_disabled_toggle_and_restricts_legacy_handles():
    from app import telegram_historical_recovery as module
    store = MemoryRuntimeStateStore()
    pid, prod = module._APPROVED_PROJECT, module._APPROVED_PRODUCT
    _legacy(store, pid, prod, sorted(module._APPROVED_HANDLES))
    project = store.get("customer_acquisition_projects", str(pid))
    project["channel_preferences"] = {"TELEGRAM": "DISABLED"}
    store.put("customer_acquisition_projects", str(pid), project)
    output = await recover(
        store=store, project_id=pid, product_id=prod,
        product=SimpleNamespace(id=prod), icps=[], connector=None,
        max_groups=9, authorized_oneoff=True,
    )
    assert output["registry_after"] == 9
    assert output["native_checked"] == 0
    assert all(v == 0 for v in output["effects"].values())
    assert store.get("customer_acquisition_projects", str(pid))[
        "channel_preferences"
    ]["TELEGRAM"] == "DISABLED"
