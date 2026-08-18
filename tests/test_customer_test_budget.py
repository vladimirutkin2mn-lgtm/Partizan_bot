from pathlib import Path

import pytest
from pydantic import ValidationError

from app.customer_schemas import CustomerPreviewRequest


def _payload(budget_usd: int) -> CustomerPreviewRequest:
    return CustomerPreviewRequest(
        brief="A customer acquisition product with enough detail for preview validation.",
        website_url="https://example.com",
        market="United States",
        goal="Validate demand",
        budget_usd=budget_usd,
    )


def test_preview_budget_accepts_small_non_round_amounts() -> None:
    assert _payload(1).budget_usd == 1
    assert _payload(37).budget_usd == 37
    assert _payload(99).budget_usd == 99


def test_preview_budget_still_requires_positive_amount() -> None:
    with pytest.raises(ValidationError):
        _payload(0)


def test_start_form_does_not_force_100_dollar_or_50_dollar_steps() -> None:
    html = Path("app/web/start.v1.html").read_text(encoding="utf-8")
    assert 'id="budget" type="number" min="1" max="100000" step="1"' in html
    assert 'id="autopilot-budget" type="number" min="100"' in html
