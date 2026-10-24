"""One-time latest-only FemDom research must not authorize audience or publisher."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.audience_intelligence_service import InMemoryAudienceIntelligenceService
from app.distribution_schemas import AudienceDistributionMapView, DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.femdom_latest_target_reconcile import _PRODUCT, _PROJECT, run
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_community_registry import TelegramCommunityRegistry
from app.telegram_research import (
    SurfaceAvailability,
    TelegramCommunitySnapshot,
    TelegramKnownCommunityInspection,
    TelegramRecentContext,
    TelegramSurfaceKind,
)


class LatestOnlyConnector:
    def __init__(self):
        self.calls = []

    async def inspect_latest_known(self, handles):
        self.calls.append(list(handles))
        now = datetime.now(UTC)
        return [
            TelegramKnownCommunityInspection(
                handle=handle,
                status="VERIFIED",
                snapshot=TelegramCommunitySnapshot(
                    entity_id=12000 + index,
                    username=handle, title=f"BDSM communities {handle}",
                    kind=TelegramSurfaceKind.CHANNEL,
                    url=f"https://t.me/{handle}",
                    about="Community event listings",
                    comment_surface=SurfaceAvailability.AVAILABLE,
                    source_checked_at=now,
                    recent_context=(TelegramRecentContext(
                        message_id=200+index,
                        text="Рекламный пост: БДСМ встреча",
                        published_at=now-timedelta(minutes=10),
                        url=f"https://t.me/{handle}/{200+index}",
                    ),),
                ),
            )
            for index, handle in enumerate(handles)
        ]

    async def inspect_known(self, *_args, **_kwargs):
        raise AssertionError("Never inspect older posts")

    async def discover(self, *_args, **_kwargs):
        raise AssertionError("Never globally discover new Telegram peers")


@pytest.mark.asyncio
async def test_oneoff_preserves_exact_research_urls_but_not_unconfirmed_action_targets():
    store = MemoryRuntimeStateStore()
    handles = ("a_sfera", "play_hard_community")
    project = {
        "id": str(_PROJECT), "product_id": str(_PRODUCT),
        "channel_preferences": {"TELEGRAM": "OFF"},
    }
    store.put("customer_acquisition_projects", str(_PROJECT), project)
    existing = [
        DistributionOpportunityView(
            id=uuid4(), icp_id=uuid4(), kind=OpportunityKind.CHANNEL,
            platform=DistributionPlatform.TELEGRAM,
            canonical_key=f"legacy:{handle}",
            title=handle, url=f"https://t.me/{handle}",
            metadata={"handle": handle, "action_target_url": None},
        )
        for handle in handles
    ]
    store.put("audience_distribution_map", str(_PRODUCT), AudienceDistributionMapView(
        product_id=_PRODUCT, top_icp_count=1,
        opportunity_count=2, opportunities=existing,
    ).model_dump(mode="json"))
    registry = TelegramCommunityRegistry(store)
    for handle in handles:
        registry.upsert(
            product_id=_PRODUCT, canonical_key=f"telegram:{handle}:community",
            title=handle, url=f"https://t.me/{handle}", kind="CHANNEL",
            metadata={"handle": handle, "native_research_status": "UNVERIFIED",
                      "community_fit_pass": False},
        )
    product = SimpleNamespace(
        id=_PRODUCT, name="FemDom", description="FemDom community matchmaking",
        market="online", language="Russian", problem_or_desire="FemDom companionship",
        value_proposition="Community connections",
        known_audience=["FemDom"],
    )
    icp = SimpleNamespace(
        id=uuid4(), title="FemDom BDSM audience", pain="Find communities",
        trigger="Interest in FemDom", alternatives=[],
    )
    connector = LatestOnlyConnector()
    output = await run(store=store, connector=connector, product=product, icp=icp)
    assert output["status"] == "FEMDOM_LATEST_ONLY_RECONCILIATION_COMPLETE"
    assert connector.calls == [list(handles)]
    assert output["llm_new_calls"] == 0
    assert output["telegram_messages_sent"] == 0
    assert output["channel_toggle_changes"] == 0
    checked = [item for item in output["communities"] if item["handle"] in handles]
    missing = [item for item in output["communities"] if item["handle"] not in handles]
    assert len(missing) == 6
    assert all(item["status"] == "MISSING_REGISTRY" for item in missing)
    assert all(item["post_read_count"] == 1 for item in checked)
    assert all(item["action_target_url"] is None for item in output["communities"])
    assert all(item["research_latest_post_url"] for item in checked)
    persisted = InMemoryAudienceIntelligenceService(store).get(_PRODUCT)
    assert {o.id for o in persisted.opportunities} == {o.id for o in existing}
    assert all(o.metadata["research_latest_post_url"] for o in persisted.opportunities)
    assert all(o.metadata["actionability"] == "RESEARCH_ONLY"
               for o in persisted.opportunities)
    assert store.get("customer_acquisition_projects", str(_PROJECT)) == project
