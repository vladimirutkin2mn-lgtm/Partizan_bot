import pytest

from app.customer_telegram_profile_setup import _build_about


def test_build_about_uses_customer_facing_cta_when_profile_is_empty() -> None:
    route = "https://partizanlabs.com/p/7a729985b6ac4e16b44fc675"

    about = _build_about(
        "",
        product_name="FemDom",
        profile_route_url=route,
    )

    assert about == f"FemDom → {route}"
    assert len(about) <= 70


def test_build_about_preserves_existing_bio_when_cta_already_present() -> None:
    route = "https://partizanlabs.com/p/7a729985b6ac4e16b44fc675"
    existing = f"FemDom → {route}"

    assert (
        _build_about(
            existing,
            product_name="FemDom",
            profile_route_url=route,
        )
        == existing
    )


def test_build_about_appends_without_overwriting_when_it_fits() -> None:
    route = "https://partizanlabs.com/p/short"
    existing = "Creator"

    about = _build_about(
        existing,
        product_name="FemDom",
        profile_route_url=route,
    )

    assert about == f"{existing}\nFemDom → {route}"


def test_build_about_refuses_destructive_overwrite_when_existing_bio_is_too_long() -> None:
    route = "https://partizanlabs.com/p/7a729985b6ac4e16b44fc675"

    with pytest.raises(ValueError, match="too long"):
        _build_about(
            "Existing biography that must not be silently replaced",
            product_name="FemDom",
            profile_route_url=route,
        )
