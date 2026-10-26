"""Offline, human-rated prompt evaluations within Growth AutoResearch.

A comparison consumes paired reviews of drafts already generated elsewhere;
it never calls an LLM, sends a message, edits the live prompt, or grants
publication permissions. Only aggregate evidence is persisted.
"""

from __future__ import annotations

import hashlib
import json
from statistics import mean

from pydantic import BaseModel, Field

from app.runtime_store import RuntimeStateStore, get_runtime_store

COMMUNITY_MESSAGE_PROMPT_EVALUATION_NAMESPACE = "growth_autoresearch_message_prompt_evaluation"
_MIN_PAIRED_REVIEWS = 6
_MIN_SCORE_LIFT = 0.3
_MAX_TOKEN_INCREASE = 1.10


class CommunityMessageGrade(BaseModel):
    """Human assessment of a single actual draft, on a fixed 0-4 rubric.

    Ratings represent specificity to the exact target, natural conversation,
    practical usefulness, and fit to an honest conversion mechanism. These
    are human annotations, not automatically inferred quality labels.
    """

    target_specificity: int = Field(ge=0, le=4)
    naturalness: int = Field(ge=0, le=4)
    usefulness: int = Field(ge=0, le=4)
    conversion_fit: int = Field(ge=0, le=4)
    author_curiosity: int | None = Field(default=None, ge=0, le=4)
    voice_consistency: int | None = Field(default=None, ge=0, le=4)
    grounded: bool
    language_matches: bool
    no_deceptive_promotion: bool
    safe_content: bool
    input_tokens: int = Field(ge=1)
    output_tokens: int = Field(ge=1)

    @property
    def score(self) -> float:
        # Preserve prior study scoring for historical comparisons. New voice
        # studies explicitly grade curiosity about the author and voice fit.
        if self.author_curiosity is not None and self.voice_consistency is not None:
            return (
                0.25 * self.target_specificity
                + 0.25 * self.naturalness
                + 0.10 * self.usefulness
                + 0.15 * self.conversion_fit
                + 0.15 * self.author_curiosity
                + 0.10 * self.voice_consistency
            )
        return (
            0.35 * self.target_specificity
            + 0.25 * self.naturalness
            + 0.25 * self.usefulness
            + 0.15 * self.conversion_fit
        )

    @property
    def has_veto(self) -> bool:
        return not all(
            (
                self.grounded,
                self.language_matches,
                self.no_deceptive_promotion,
                self.safe_content,
            )
        )


class CommunityMessagePairedReview(BaseModel):
    """Two independently prepared drafts reviewed against the same target."""

    case_id: str = Field(min_length=6, max_length=96)
    baseline: CommunityMessageGrade
    challenger: CommunityMessageGrade


class CommunityMessagePromptComparison(BaseModel):
    comparison_id: str
    baseline_version: str
    challenger_version: str
    review_count: int
    status: str
    baseline_mean_score: float
    challenger_mean_score: float
    score_lift: float
    baseline_total_tokens: int
    challenger_total_tokens: int
    relative_token_change: float
    challenger_veto_count: int
    production_prompt_unchanged: bool = True
    requires_manual_approval: bool = True


class CommunityMessagePromptAutoResearch:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store if store is not None else get_runtime_store()

    def evaluate(
        self,
        *,
        baseline_version: str,
        challenger_version: str,
        reviews: list[CommunityMessagePairedReview],
    ) -> CommunityMessagePromptComparison:
        """Compare identical target cases. Never evaluate against unmatched posts."""
        if (
            not baseline_version.startswith("community_message_")
            or not challenger_version.startswith("community_message_")
            or baseline_version == challenger_version
        ):
            raise ValueError("Distinct, named community message prompt versions required")
        cases = [CommunityMessagePairedReview.model_validate(row) for row in reviews]
        if len(set(row.case_id for row in cases)) != len(cases):
            raise ValueError("Paired benchmark cases must be unique")
        if not cases:
            raise ValueError("At least one human-reviewed prompt pair is required")
        # Comparing a curiosity/voice study with an older rubric would
        # manufacture an apparent lift from different scoring methodologies.
        graded = [
            grade for pair in cases for grade in (pair.baseline, pair.challenger)
        ]
        has_curiosity = [
            grade.author_curiosity is not None
            and grade.voice_consistency is not None
            for grade in graded
        ]
        if any(
            (grade.author_curiosity is None) != (grade.voice_consistency is None)
            for grade in graded
        ) or (any(has_curiosity) and not all(has_curiosity)):
            raise ValueError("Paired reviews must use the same complete voice rubric")
        cases.sort(key=lambda item: item.case_id)

        baseline_score = mean(row.baseline.score for row in cases)
        challenger_score = mean(row.challenger.score for row in cases)
        baseline_tokens = sum(
            row.baseline.input_tokens + row.baseline.output_tokens for row in cases
        )
        challenger_tokens = sum(
            row.challenger.input_tokens + row.challenger.output_tokens for row in cases
        )
        relative_change = challenger_tokens / baseline_tokens - 1
        veto_count = sum(row.challenger.has_veto for row in cases)
        lift = challenger_score - baseline_score

        if veto_count:
            status = "SAFETY_OR_GROUNDING_VETO"
        elif len(cases) < _MIN_PAIRED_REVIEWS:
            status = "INSUFFICIENT_PAIRED_REVIEWS"
        elif relative_change > _MAX_TOKEN_INCREASE - 1:
            status = "TOKEN_COST_VETO"
        elif lift < _MIN_SCORE_LIFT:
            status = "NO_MEANINGFUL_QUALITY_LIFT"
        else:
            status = "CANDIDATE_FOR_MANUAL_PROMOTION"

        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "baseline": baseline_version,
                    "challenger": challenger_version,
                    "reviews": [row.model_dump(mode="json") for row in cases],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        result = CommunityMessagePromptComparison(
            comparison_id=fingerprint[:24],
            baseline_version=baseline_version,
            challenger_version=challenger_version,
            review_count=len(cases),
            status=status,
            baseline_mean_score=round(baseline_score, 3),
            challenger_mean_score=round(challenger_score, 3),
            score_lift=round(lift, 3),
            baseline_total_tokens=baseline_tokens,
            challenger_total_tokens=challenger_tokens,
            relative_token_change=round(relative_change, 4),
            challenger_veto_count=veto_count,
        )
        # Do not write case IDs, source post content, comments, or product facts.
        self._store.put_if_absent(
            COMMUNITY_MESSAGE_PROMPT_EVALUATION_NAMESPACE,
            result.comparison_id,
            result.model_dump(mode="json"),
        )
        return result
