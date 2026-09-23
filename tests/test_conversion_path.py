from types import SimpleNamespace

import pytest

from app.conversion_path import (
    ConversionPathStatus,
    conversion_path_validator,
)
from app.conversion_scenarios import ConversionMechanism
from app.distribution_execution_service import distribution_execution_service


def _action(
    *,
    tracking_url: str = "https://partizan.example/r/abc123",
    text: str = "Useful",
) -> SimpleNamespace:
    return SimpleNamespace(
        tracking_url=tracking_url,
        content_text=text,
    )


def _identity(profile_config: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(profile_config=profile_config or {})


def test_profile_click_requires_verified_profile_handoff() -> None:
    assessment = conversion_path_validator.assess(
        mechanism=ConversionMechanism.PROFILE_CLICK,
        action=_action(),
        identity=_identity(),
        slot=SimpleNamespace(),
    )

    assert assessment.status == ConversionPathStatus.SETUP_REQUIRED
    assert assessment.send_eligible is False
    assert assessment.required_profile_url == "https://partizan.example/r/abc123"
    assert any("profile conversion CTA" in item for item in assessment.blockers)


def test_profile_click_is_ready_when_profile_points_to_exact_tracking_url() -> None:
    tracking_url = "https://partizan.example/r/abc123"
    assessment = conversion_path_validator.assess(
        mechanism=ConversionMechanism.PROFILE_CLICK,
        action=_action(tracking_url=tracking_url),
        identity=_identity(
            {
                "conversion_profile_verified": True,
                "conversion_profile_url": tracking_url,
            }
        ),
        slot=SimpleNamespace(),
    )

    assert assessment.status == ConversionPathStatus.READY
    assert assessment.send_eligible is True
    assert assessment.blockers == ()


def test_reply_engagement_still_requires_measurable_product_handoff() -> None:
    assessment = conversion_path_validator.assess(
        mechanism=ConversionMechanism.REPLY_ENGAGEMENT,
        action=_action(),
        identity=_identity(),
        slot=SimpleNamespace(),
    )

    assert assessment.status == ConversionPathStatus.SETUP_REQUIRED
    assert "Public reply engagement" in assessment.steps
    assert assessment.required_profile_url


def test_brand_search_is_blocked_without_verified_attribution() -> None:
    assessment = conversion_path_validator.assess(
        mechanism=ConversionMechanism.BRAND_SEARCH,
        action=_action(),
        identity=_identity(),
        slot=SimpleNamespace(),
    )

    assert assessment.status == ConversionPathStatus.BLOCKED
    assert assessment.send_eligible is False


def test_direct_link_requires_tracking_url_in_exact_draft() -> None:
    tracking_url = "https://partizan.example/r/abc123"
    missing = conversion_path_validator.assess(
        mechanism=ConversionMechanism.DIRECT_LINK,
        action=_action(tracking_url=tracking_url, text="No link here"),
        identity=None,
        slot=None,
    )
    ready = conversion_path_validator.assess(
        mechanism=ConversionMechanism.DIRECT_LINK,
        action=_action(tracking_url=tracking_url, text=f"Useful: {tracking_url}"),
        identity=None,
        slot=None,
    )

    assert missing.status == ConversionPathStatus.SETUP_REQUIRED
    assert ready.status == ConversionPathStatus.READY


def test_action_approval_validation_fails_closed_for_unready_conversion_path() -> None:
    action = SimpleNamespace(
        content_payload={
            "conversion_mechanism": "PROFILE_CLICK",
            "conversion_path_status": "SETUP_REQUIRED",
            "conversion_path": {
                "blockers": ["Telegram profile conversion CTA has not been verified"]
            },
        },
    )

    with pytest.raises(ValueError, match="Conversion path must be READY"):
        distribution_execution_service._validate_action_ready_for_approval(action)
