"""Research prompt quality with paired, human-rated offline examples."""

import pytest

from app.community_message_autoresearch import (
    COMMUNITY_MESSAGE_PROMPT_EVALUATION_NAMESPACE,
    CommunityMessageGrade,
    CommunityMessagePairedReview,
)
from app.growth_autoresearch import GrowthAutoResearchService
from app.runtime_store import MemoryRuntimeStateStore


def _grade(*, strong, tokens=100, grounded=True, language=True, safe=True):
    return CommunityMessageGrade(
        target_specificity=4 if strong else 1,
        naturalness=4 if strong else 2,
        usefulness=3 if strong else 1,
        conversion_fit=3 if strong else 1,
        grounded=grounded,
        language_matches=language,
        no_deceptive_promotion=True,
        safe_content=safe,
        input_tokens=tokens,
        output_tokens=35,
    )


def _reviews(count=6, *, challenger_tokens=90, grounded=True, safe=True):
    return [
        CommunityMessagePairedReview(
            case_id="case-" + str(i).zfill(3),
            baseline=_grade(strong=False),
            challenger=_grade(
                strong=True,
                tokens=challenger_tokens,
                grounded=grounded,
                safe=safe,
            ),
        )
        for i in range(count)
    ]


def _evaluate(service, reviews):
    return service.evaluate_telegram_message_prompts(
        baseline_version="community_message_v3_single_local_language",
        challenger_version="community_message_v4_grounded_target",
        reviews=reviews,
    )


def test_paired_review_returns_candidate_without_changing_production_prompt():
    store = MemoryRuntimeStateStore()
    service = GrowthAutoResearchService(store=store)
    result = _evaluate(service, _reviews())
    assert result.status == "CANDIDATE_FOR_MANUAL_PROMOTION"
    assert result.review_count == 6
    assert result.score_lift > 0
    assert result.relative_token_change < 0
    assert result.production_prompt_unchanged is True
    assert result.requires_manual_approval is True

    # AutoResearch persists aggregate only, not the post or individual reviewer labels.
    rows = store.list_namespace(COMMUNITY_MESSAGE_PROMPT_EVALUATION_NAMESPACE)
    assert len(rows) == 1
    assert "case-000" not in str(rows)
    assert "target_specificity" not in str(rows)
    assert _evaluate(service, list(reversed(_reviews()))).comparison_id == result.comparison_id
    assert len(store.list_namespace(COMMUNITY_MESSAGE_PROMPT_EVALUATION_NAMESPACE)) == 1


@pytest.mark.parametrize(
    ("kwargs", "status"),
    [
        ({"count": 3}, "INSUFFICIENT_PAIRED_REVIEWS"),
        ({"grounded": False}, "SAFETY_OR_GROUNDING_VETO"),
        ({"safe": False}, "SAFETY_OR_GROUNDING_VETO"),
        ({"challenger_tokens": 200}, "TOKEN_COST_VETO"),
    ],
)
def test_comparison_is_fail_closed_on_weak_evidence_safety_and_cost(kwargs, status):
    result = _evaluate(
        GrowthAutoResearchService(store=MemoryRuntimeStateStore()),
        _reviews(**kwargs),
    )
    assert result.status == status


def test_duplicate_cases_cannot_inflate_benchmark_sample():
    service = GrowthAutoResearchService(store=MemoryRuntimeStateStore())
    cases = _reviews()
    with pytest.raises(ValueError, match="unique"):
        _evaluate(service, cases + [cases[0]])


def test_same_prompt_version_is_rejected():
    service = GrowthAutoResearchService(store=MemoryRuntimeStateStore())
    with pytest.raises(ValueError, match="Distinct"):
        service.evaluate_telegram_message_prompts(
            baseline_version="community_message_v4_grounded_target",
            challenger_version="community_message_v4_grounded_target",
            reviews=_reviews(),
        )
