from argparse import Namespace
from pathlib import Path

import pytest

from app.growth_run import (
    GrowthRunReport,
    _create_and_confirm_product,
    _execute_if_requested,
    build_parser,
    clarification_answers,
    parse_answer,
    read_brief,
    select_play,
    summarize_blocked_plays,
)


class StubClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None, bool]] = []

    def post(
        self,
        path: str,
        *,
        body: dict | None = None,
        query: dict | None = None,
        operator: bool = False,
    ) -> dict:
        del query
        self.calls.append(("POST", path, body, operator))
        return self.responses.pop(0)


def test_parse_answer_and_duplicate_guard() -> None:
    assert parse_answer("market=US") == ("market", "US")
    assert parse_answer(" LANGUAGE = English ") == ("language", "English")
    with pytest.raises(ValueError, match="duplicate clarification"):
        clarification_answers([("market", "US"), ("market", "UK")])


@pytest.mark.parametrize("value", ["", "field", "=value", "field="])
def test_parse_answer_rejects_incomplete_values(value: str) -> None:
    with pytest.raises(Exception, match="FIELD=VALUE"):
        parse_answer(value)


def test_read_brief_supports_inline_and_file(tmp_path: Path) -> None:
    inline = Namespace(brief="  inline product  ", brief_file=None)
    assert read_brief(inline) == "inline product"

    path = tmp_path / "product.md"
    path.write_text("  file product\n", encoding="utf-8")
    from_file = Namespace(brief=None, brief_file=str(path))
    assert read_brief(from_file) == "file product"


def test_parser_has_generic_product_sources_and_no_secret_arguments() -> None:
    parser = build_parser()
    args = parser.parse_args(["--brief", "My product"])

    assert args.brief == "My product"
    assert args.product_id is None
    assert args.execute is False
    option_strings = {option for action in parser._actions for option in action.option_strings}
    assert "--product-id" in option_strings
    assert "--brief" in option_strings
    assert "--brief-file" in option_strings
    assert "--answer" in option_strings
    assert "--operator-key" not in option_strings
    assert "--provider-token" not in option_strings
    assert "--access-token" not in option_strings
    assert "--price" not in option_strings


def test_parser_rejects_multiple_product_sources() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--product-id", "abc", "--brief", "product"])


def test_create_product_requires_explicit_unknown_clarification_answer() -> None:
    client = StubClient(
        [
            {
                "product": {"id": "product-1", "status": "DRAFT"},
                "clarifications": [
                    {
                        "id": "question-1",
                        "field_name": "market",
                        "question": "Which market?",
                    }
                ],
            }
        ]
    )
    report = GrowthRunReport()

    product = _create_and_confirm_product(
        client,  # type: ignore[arg-type]
        brief="Generic product",
        reference_links=[],
        answers={},
        report=report,
    )

    assert product["id"] == "product-1"
    assert report.product_id == "product-1"
    assert report.blockers == [
        "Missing explicit clarification answer. Re-run with --answer 'market=<value>' for: Which market?"
    ]
    assert len(client.calls) == 1


def test_create_product_uses_only_explicit_clarification_answers() -> None:
    client = StubClient(
        [
            {
                "product": {"id": "product-1", "status": "DRAFT"},
                "clarifications": [
                    {
                        "id": "question-1",
                        "field_name": "language",
                        "question": "Which language?",
                    }
                ],
            },
            {
                "product": {"id": "product-1", "status": "DRAFT"},
                "clarifications": [],
            },
            {"product": {"id": "product-1", "status": "CONFIRMED"}},
        ]
    )
    report = GrowthRunReport()

    product = _create_and_confirm_product(
        client,  # type: ignore[arg-type]
        brief="Generic product",
        reference_links=[],
        answers={"language": "English"},
        report=report,
    )

    assert product["status"] == "CONFIRMED"
    assert report.blockers == []
    assert client.calls[1][2] == {"question_id": "question-1", "answer": "English"}


def test_select_play_uses_ready_only_and_filters() -> None:
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
            "platform": "REDDIT",
            "tactic_class": "COMMUNITY",
            "priority_score": 80,
            "blockers": ["CommunityPolicy required"],
        }
    ]
    assert summarize_blocked_plays(plays) == [
        "REDDIT / COMMUNITY: CommunityPolicy required"
    ]


def test_execute_path_stops_at_existing_adapter_boundary() -> None:
    client = StubClient(
        [
            {},
            {
                "receipt": {
                    "outcome": "STAGED",
                    "message": "provider objects created paused",
                }
            },
        ]
    )
    report = GrowthRunReport(action_id="action-1")

    _execute_if_requested(client, report, execute=True)  # type: ignore[arg-type]

    assert client.calls == [
        ("POST", "/v1/distribution-actions/action-1/approve", None, True),
        (
            "POST",
            "/v1/distribution-actions/action-1/execute",
            {"retry": False},
            True,
        ),
    ]
    assert any("never activates spend" in note for note in report.notes)


def test_runner_source_is_generic_and_contains_no_paid_activation_path() -> None:
    from app import growth_run

    source = Path(growth_run.__file__).read_text(encoding="utf-8")
    assert "Oracle" not in source
    assert "/paid-campaign/activate" not in source
    assert "activation-authorizations" not in source
    assert "approved_budget_cap" not in source
    assert "PARTIZAN_OPERATOR_KEY" in source
    assert "OPERATOR_API_KEY" in source
    assert 'body={"retry": False}' in source
