from types import SimpleNamespace
from uuid import uuid4

from app.distribution_play_schemas import DistributionPlayStatus
from app.distribution_play_service import InMemoryDistributionPlayService
from app.distribution_schemas import (
    AudienceDistributionMapView,
    DistributionIdentityView,
    DistributionOpportunityView,
)
from app.distribution_types import (
    DistributionIdentityStatus,
    DistributionPlatform,
    OpportunityKind,
)
from app.runtime_store import MemoryRuntimeStateStore


def _product():
    return SimpleNamespace(
        id=uuid4(),
        goal="Acquire paid users",
        budget=200.0,
        max_cac=10.0,
        language="English",
    )


def _identity() -> DistributionIdentityView:
    return DistributionIdentityView(
        id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        theme="Telegram community",
        language="English",
        public_positioning="Customer Telegram identity",
        profile_config={
            "publisher_mode": "CLIENT_OWNED",
            "customer_project_id": str(uuid4()),
        },
        status=DistributionIdentityStatus.ACTIVE,
    )


def _standalone_opportunity(*, preflight_status: str, stale_permission: bool = False):
    return DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=OpportunityKind.GROUP,
        canonical_key="telegram:42:community",
        title="Telegram community",
        url="https://t.me/community",
        relevance_score=90,
        rationale="Relevant audience",
        metadata={
            "native_research_status": "VERIFIED",
            "action_target_url": "https://t.me/community",
            "action_target_specific": False,
            "action_target_mode": "STANDALONE_POST",
            "surface_capabilities": {
                "reply": "UNAVAILABLE",
                "standalone_post": "AVAILABLE",
                "publisher_permission_verified": stale_permission,
            },
            "publisher_participation_preflight": {
                "status": preflight_status,
                "action_target_mode": "STANDALONE_POST",
            },
        },
    )


def _map(product_id, opportunity):
    return AudienceDistributionMapView(
        product_id=product_id,
        top_icp_count=1,
        opportunity_count=1,
        opportunities=[opportunity],
    )


def test_ready_standalone_preflight_promotes_verified_publisher_permission() -> None:
    product = _product()
    opportunity = _standalone_opportunity(preflight_status="READY")
    service = InMemoryDistributionPlayService(MemoryRuntimeStateStore())

    normalized = service._apply_telegram_publisher_preflight(
        _map(product.id, opportunity)
    )

    capabilities = normalized.opportunities[0].metadata["surface_capabilities"]
    assert capabilities["standalone_post"] == "AVAILABLE"
    assert capabilities["publisher_permission_verified"] is True

    response = service.generate(
        product,
        normalized,
        identities=[_identity()],
    )
    post = next(play for play in response.plays if play.tactic_id == "telegram_group_post")
    assert post.status == DistributionPlayStatus.READY
    assert not any("publisher permission" in reason.lower() for reason in post.blockers)


def test_failed_standalone_preflight_revokes_stale_publisher_permission() -> None:
    product = _product()
    opportunity = _standalone_opportunity(
        preflight_status="NO_WRITE_ACCESS",
        stale_permission=True,
    )
    service = InMemoryDistributionPlayService(MemoryRuntimeStateStore())

    normalized = service._apply_telegram_publisher_preflight(
        _map(product.id, opportunity)
    )

    capabilities = normalized.opportunities[0].metadata["surface_capabilities"]
    assert capabilities["standalone_post"] == "UNAVAILABLE"
    assert capabilities["publisher_permission_verified"] is False

    response = service.generate(
        product,
        normalized,
        identities=[_identity()],
    )
    post = next(play for play in response.plays if play.tactic_id == "telegram_group_post")
    assert post.status == DistributionPlayStatus.BLOCKED
    assert any("publisher permission" in reason.lower() for reason in post.blockers)
