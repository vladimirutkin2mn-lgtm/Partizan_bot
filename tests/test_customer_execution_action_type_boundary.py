from types import SimpleNamespace
from uuid import UUID

import pytest

import app.creative_provider_finalization as creative_provider_finalization_module
from app.customer_execution_boundary import (
    customer_execution_request_scope,
    require_customer_bound_mutation_scope,
)
from app.distribution_types import DistributionActionType

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
ACTION_ID = UUID("33333333-3333-4333-8333-333333333333")


def _action(action_type: DistributionActionType, *, confirmed: bool = True):
    metadata = {
        "customer_execution_request_id": str(REQUEST_ID),
        "customer_publish_confirmation_required": True,
    }
    if confirmed:
        metadata["customer_publish_confirmed_at"] = "2026-09-14T12:00:00+00:00"
    return SimpleNamespace(
        action_type=action_type,
        operational_metadata=metadata,
    )


@pytest.mark.parametrize(
    "action_type",
    [
        DistributionActionType.PAID_CAMPAIGN,
        DistributionActionType.OUTREACH_EMAIL,
    ],
)
def test_unsupported_customer_action_type_fails_even_inside_matching_scope(
    action_type: DistributionActionType,
) -> None:
    action = _action(action_type)

    with customer_execution_request_scope(REQUEST_ID):
        with pytest.raises(ValueError, match=f"cannot mutate {action_type.value} actions"):
            require_customer_bound_mutation_scope(action, "execution")


def test_unsupported_customer_action_type_fails_before_generic_scope_error() -> None:
    action = _action(DistributionActionType.PAID_CAMPAIGN)

    with pytest.raises(ValueError, match="cannot mutate PAID_CAMPAIGN actions"):
        require_customer_bound_mutation_scope(action, "execution")


def test_preconfirmation_approval_preserves_customer_confirmation_error() -> None:
    action = _action(DistributionActionType.OUTREACH_EMAIL, confirmed=False)

    with customer_execution_request_scope(REQUEST_ID):
        with pytest.raises(ValueError, match="Customer publish confirmation"):
            require_customer_bound_mutation_scope(action, "approval")


def test_supported_customer_action_still_uses_matching_request_scope() -> None:
    action = _action(DistributionActionType.REPLY)

    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        require_customer_bound_mutation_scope(action, "execution")

    with customer_execution_request_scope(REQUEST_ID):
        require_customer_bound_mutation_scope(action, "execution")


def test_tiktok_creative_finalization_blocks_customer_paid_before_asset_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = _action(DistributionActionType.PAID_CAMPAIGN)
    monkeypatch.setattr(
        creative_provider_finalization_module.distribution_execution_service,
        "get_action",
        lambda action_id: action,
    )
    asset_service = SimpleNamespace(
        readiness=lambda action_id: pytest.fail(
            "creative readiness must not run for a customer-bound paid action"
        )
    )
    finalizer = creative_provider_finalization_module.TikTokVideoCreativeFinalizer(
        asset_service=asset_service,
    )

    with pytest.raises(ValueError, match="cannot mutate PAID_CAMPAIGN actions"):
        finalizer.finalize(ACTION_ID)
