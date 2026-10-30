"""Conservative checks for campaign metadata mistaken for product value.

This check never invents product facts. It only keeps acquisition setup fields
from being treated as evidence about what a customer receives.
"""
from __future__ import annotations

import re

_CAMPAIGN_ASSET_LABEL = re.compile(
    r"(?i)\b(?:promoted\s+asset|asset\s+type)\s*:"
)
_PROMOTIONAL_OPERATIONAL_FIELDS = re.compile(
    r"(?i)\b(?:test\s+budget|advertising\s+budget|"
    r"acquisition\s+budget|campaign\s+budget)\s*:"
)


def is_campaign_asset_brief(text: str) -> bool:
    """Campaign/asset wrapper is not proof of what a visitor can consume."""
    return bool(
        _CAMPAIGN_ASSET_LABEL.search(text or "")
        or _PROMOTIONAL_OPERATIONAL_FIELDS.search(text or "")
    )


def is_distinct_product_value(
    description: str | None, value_proposition: str | None
) -> bool:
    """A campaign wrapper copied to the value field is not user value."""
    value = " ".join(str(value_proposition or "").split()).strip()
    baseline = " ".join(str(description or "").split()).strip()
    if len(value) < 12 or value.casefold() == baseline.casefold():
        return False
    return not is_campaign_asset_brief(value)


def needs_product_value_clarification(
    *, description: str | None, value_proposition: str | None,
    problem_or_desire: str | None, source_brief: str,
) -> bool:
    """Ask only when the campaign wrapper lacks a distinct value AND need."""
    if not is_campaign_asset_brief(source_brief):
        return False
    if is_distinct_product_value(description, value_proposition):
        return False
    need = " ".join(str(problem_or_desire or "").split())
    return len(need) < 20 or is_campaign_asset_brief(need)
