from argparse import Namespace

from app.oracle_dogfood import (
    DEFAULT_BUDGET,
    DEFAULT_MAX_CAC,
    DEFAULT_PRICE,
    build_parser,
    clarification_answer,
    oracle_brief,
    select_play,
    summarize_blocked_plays,
)


def test_oracle_brief_contains_business_guardrails() -> None:
    brief = oracle_brief(price=DEFAULT_PRICE, budget=DEFAULT_BUDGET, max_cac=DEFAULT_MAX_CAC)

    assert "$6.90" in brief
    assert "$1000.00" in brief
    assert "$12.00" in brief
    assert "first 100 paying subscribers" in brief
    assert "Telegram, Instagram, Reddit and TikTok" in brief
    assert "entertainment/reflection" in brief.lower()


def test_known_clarifications_are_answered_without_inventing_unknown_fields() -> None:
    assert clarification_answer("budget", budget=1000, max_cac=12, price=6.9) == (
        "1000.00 USD initial acquisition test budget"
    )
    assert clarification_answer("max_cac", budget=1000, max_cac=12, price=6.9) == (
        "12.00 USD maximum CAC per paid subscriber"
    )
    assert clarification_answer("language", budget=1000, max_cac=12, price=6.9) == "English"
    assert clarification_answer("unexpected_private_fact", budget=1000, max_cac=12, price=6.9) is None


def test_select_play_uses_ready_only_and_highest_priority() -> None:
    plays = [
        {
            "id": "blocked-high",
            "status": "BLOCKED",
            "platform": "INSTAGRAM",
            "tactic_class": "PAID_PLATFORM",
            "priority_score": 99,
        },
        {
            "id": "ready-low",
            "status": "READY",
            "platform": "INSTAGRAM",
            "tactic_class": "PAID_PLATFORM",
            "priority_score": 62,
        },
        {
            "id": "ready-high",
            "status": "READY",
            "platform": "TIKTOK",
            "tactic_class": "OWNED_ORGANIC",
            "priority_score": 88,
        },
    ]

    assert select_play(plays)["id"] == "ready-high"
    assert select_play(plays, platform="INSTAGRAM")["id"] == "ready-low"
    assert select_play(plays, tactic_class="PAID_PLATFORM")["id"] == "ready-low"
    assert select_play(plays, platform="REDDIT") is None


def test_blocker_summary_surfaces_setup_requirements() -> None:
    plays = [
        {
            "status": "BLOCKED",
            "platform": "INSTAGRAM",
            "tactic_class": "COMMUNITY",
            "priority_score": 90,
            "blockers": ["No eligible Distribution Identity", "No active CampaignSlot"],
        },
        {
            "status": "BLOCKED",
            "platform": "REDDIT",
            "tactic_class": "COMMUNITY",
            "priority_score": 70,
            "blockers": ["CommunityPolicy required"],
        },
    ]

    blockers = summarize_blocked_plays(plays)

    assert blockers[0].startswith("INSTAGRAM / COMMUNITY")
    assert "Distribution Identity" in blockers[0]
    assert any("CommunityPolicy required" in item for item in blockers)


def test_cli_has_no_secret_argument_and_execution_is_opt_in() -> None:
    parser = build_parser()
    args = parser.parse_args([])

    assert isinstance(args, Namespace)
    assert args.execute is False
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert "--operator-key" not in option_strings
    assert "--provider-token" not in option_strings
    assert "--access-token" not in option_strings


def test_runner_source_contains_no_paid_activation_endpoint() -> None:
    from app import oracle_dogfood

    source = open(oracle_dogfood.__file__, encoding="utf-8").read()

    assert "/paid-campaign/activate" not in source
    assert "activation-authorizations" not in source
    assert "approved_budget_cap" not in source
    assert "PARTIZAN_OPERATOR_KEY" in source
    assert "OPERATOR_API_KEY" in source
    assert 'body={"retry": False}' in source
