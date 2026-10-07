import pytest

from app.customer_telegram_review_bundle import (
    _native_destination,
    _profile_about,
    _recommended_variant,
)


def test_native_destination_prefers_real_telegram_link_over_partizan_route() -> None:
    destination = _native_destination(
        [
            "https://partizanlabs.com/femdom",
            "https://t.me/femdom_channel",
            "https://example.com/femdom",
        ]
    )

    assert destination == "https://t.me/femdom_channel"


def test_native_destination_rejects_partizan_only_reference_links() -> None:
    with pytest.raises(ValueError, match="real product destination"):
        _native_destination(["https://partizanlabs.com/femdom"])


def test_profile_about_is_exact_native_cta_and_respects_limit() -> None:
    assert _profile_about("FemDom", "https://t.me/femdom") == (
        "FemDom ↓\nhttps://t.me/femdom"
    )

    with pytest.raises(ValueError, match="bio limit"):
        _profile_about("FemDom", "https://example.com/" + "x" * 80)


def test_recommended_variant_prefers_sendable_profile_click_expertise_signal() -> None:
    variants = [
        {
            "conversion_mechanism": "REPLY_ENGAGEMENT",
            "variant_name": "thoughtful_question",
            "send_eligible": True,
            "content_text": "Question",
        },
        {
            "conversion_mechanism": "PROFILE_CLICK",
            "variant_name": "expertise_signal",
            "send_eligible": True,
            "content_text": "Expertise",
        },
        {
            "conversion_mechanism": "PROFILE_CLICK",
            "variant_name": "non_obvious_lens",
            "send_eligible": False,
            "content_text": "Lens",
        },
    ]

    assert _recommended_variant(variants)["content_text"] == "Expertise"


def test_recommended_variant_never_drops_all_variants_when_legacy_sendability_is_stale() -> None:
    variants = [
        {
            "conversion_mechanism": "PROFILE_CLICK",
            "variant_name": "expertise_signal",
            "send_eligible": False,
            "content_text": "Exact text still needs human review",
        }
    ]

    selected = _recommended_variant(variants)

    assert selected["content_text"] == "Exact text still needs human review"
