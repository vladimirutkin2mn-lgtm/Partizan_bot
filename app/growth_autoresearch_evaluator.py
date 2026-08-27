from __future__ import annotations

from dataclasses import dataclass

from app.growth_autoresearch_schemas import (
    GrowthResearchEvidence,
    GrowthResearchOutcome,
    GrowthResearchPolicyView,
)


@dataclass(frozen=True, slots=True)
class ShadowEvaluationResult:
    outcome: GrowthResearchOutcome
    rationale: list[str]
    champion_cac: float | None
    challenger_cac: float | None


class GrowthAutoResearchEvaluator:
    def evaluate(
        self,
        *,
        policy: GrowthResearchPolicyView,
        champion: GrowthResearchEvidence,
        challenger: GrowthResearchEvidence,
        blocked_reason: str | None = None,
        failed_reason: str | None = None,
    ) -> ShadowEvaluationResult:
        champion_cac = self._cac(champion)
        challenger_cac = self._cac(challenger)

        if blocked_reason:
            return ShadowEvaluationResult(
                outcome=GrowthResearchOutcome.BLOCKED,
                rationale=[blocked_reason],
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
            )
        if failed_reason:
            return ShadowEvaluationResult(
                outcome=GrowthResearchOutcome.FAILED,
                rationale=[failed_reason],
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
            )

        minimum = policy.min_paid_users_for_decision
        if challenger.paid_users < minimum:
            return ShadowEvaluationResult(
                outcome=GrowthResearchOutcome.INCONCLUSIVE,
                rationale=[
                    "Challenger does not have enough paid-user evidence for promotion: "
                    f"{challenger.paid_users} observed, {minimum} required."
                ],
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
            )

        if challenger_cac is None:
            return ShadowEvaluationResult(
                outcome=GrowthResearchOutcome.INCONCLUSIVE,
                rationale=["Challenger CAC is not measurable from the supplied evidence."],
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
            )

        if champion.paid_users < minimum or champion_cac is None:
            return ShadowEvaluationResult(
                outcome=GrowthResearchOutcome.KEEP,
                rationale=[
                    "Challenger has decision-grade paid-user evidence while the current champion "
                    "does not; promote it as the new measurable baseline."
                ],
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
            )

        threshold = policy.min_relative_cac_improvement
        if champion_cac == 0:
            if challenger_cac == 0 and challenger.paid_users > champion.paid_users:
                return ShadowEvaluationResult(
                    outcome=GrowthResearchOutcome.KEEP,
                    rationale=[
                        "Both variants have zero observed acquisition spend, and the challenger "
                        "produced more paid users."
                    ],
                    champion_cac=champion_cac,
                    challenger_cac=challenger_cac,
                )
            return ShadowEvaluationResult(
                outcome=GrowthResearchOutcome.DISCARD,
                rationale=[
                    "Current champion has zero observed CAC and the challenger did not improve "
                    "that result."
                ],
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
            )

        relative_change = (champion_cac - challenger_cac) / champion_cac
        if relative_change >= threshold:
            return ShadowEvaluationResult(
                outcome=GrowthResearchOutcome.KEEP,
                rationale=[
                    f"Challenger CAC improved by {relative_change:.1%}, meeting the "
                    f"{threshold:.1%} promotion threshold."
                ],
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
            )
        if relative_change <= -threshold:
            return ShadowEvaluationResult(
                outcome=GrowthResearchOutcome.DISCARD,
                rationale=[
                    f"Challenger CAC regressed by {abs(relative_change):.1%}, meeting the "
                    f"{threshold:.1%} discard threshold."
                ],
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
            )
        return ShadowEvaluationResult(
            outcome=GrowthResearchOutcome.INCONCLUSIVE,
            rationale=[
                f"CAC difference of {relative_change:.1%} is inside the ±{threshold:.1%} "
                "materiality band."
            ],
            champion_cac=champion_cac,
            challenger_cac=challenger_cac,
        )

    @staticmethod
    def _cac(evidence: GrowthResearchEvidence) -> float | None:
        if evidence.paid_users <= 0:
            return None
        return round(evidence.spend / evidence.paid_users, 4)


growth_autoresearch_evaluator = GrowthAutoResearchEvaluator()
