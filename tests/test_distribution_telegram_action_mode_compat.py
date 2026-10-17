from uuid import uuid4

from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import DistributionPlatform, OpportunityKind


def _opportunity(kind: OpportunityKind, metadata: dict) -> DistributionOpportunityView:
    return DistributionOpportunityView(
        id=uuid4(),
        icp_id=uuid4(),
        platform=DistributionPlatform.TELEGRAM,
        kind=kind,
        canonical_key=f"telegram:{uuid4()}",
        title="Legacy Telegram opportunity",
        url="https://t.me/community",
        metadata=metadata,
    )


def test_legacy_channel_specific_target_infers_channel_comment() -> None:
    opportunity = _opportunity(
        OpportunityKind.CHANNEL,
        {
            "action_target_url": "https://t.me/community/10",
            "action_target_specific": True,
            "surface_capabilities": {"comment": "AVAILABLE"},
        },
    )

    assert opportunity.metadata["action_target_mode"] == "CHANNEL_COMMENT"


def test_legacy_group_specific_target_prefers_reply() -> None:
    opportunity = _opportunity(
        OpportunityKind.GROUP,
        {
            "action_target_url": "https://t.me/community/10",
            "action_target_specific": True,
            "surface_capabilities": {
                "reply": "AVAILABLE",
                "standalone_post": "AVAILABLE",
            },
        },
    )

    assert opportunity.metadata["action_target_mode"] == "GROUP_REPLY"


def test_legacy_group_community_target_infers_standalone_post() -> None:
    opportunity = _opportunity(
        OpportunityKind.GROUP,
        {
            "action_target_url": "https://t.me/community",
            "action_target_specific": False,
            "surface_capabilities": {"standalone_post": "AVAILABLE"},
        },
    )

    assert opportunity.metadata["action_target_mode"] == "STANDALONE_POST"


def test_explicit_action_mode_is_never_overwritten() -> None:
    opportunity = _opportunity(
        OpportunityKind.GROUP,
        {
            "action_target_url": "https://t.me/community",
            "action_target_specific": False,
            "action_target_mode": "STANDALONE_POST",
            "surface_capabilities": {
                "reply": "AVAILABLE",
                "standalone_post": "AVAILABLE",
            },
        },
    )

    assert opportunity.metadata["action_target_mode"] == "STANDALONE_POST"
