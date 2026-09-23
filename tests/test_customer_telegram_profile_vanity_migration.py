from app.customer_telegram_profile_vanity_migration import _desired_about


def test_desired_about_is_short_human_readable_cta() -> None:
    vanity_url = "https://partizanlabs.com/femdom"

    about = _desired_about("FemDom", vanity_url)

    assert about == "FemDom ↓\nhttps://partizanlabs.com/femdom"
    assert "7a729985" not in about
    assert len(about) <= 70
