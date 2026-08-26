from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from statistics import NormalDist

from app.growth_autoresearch_schemas import (
    GrowthResearchEvidence,
    GrowthResearchObjective,
    GrowthResearchOutcome,
    GrowthResearchPolicyView,
)


@dataclass(frozen=True, slots=True)
class ShadowEvaluationResult:
    outcome: GrowthResearchOutcome
    objective: GrowthResearchObjective
    rationale: list[str]
    champion_cac: float | None
    challenger_cac: float | None
    champion_roas: float | None
    challenger_roas: float | None
    champion_metric_value: float | None
    challenger_metric_value: float | None
    relative_improvement: float | None
    confidence: float | None


class GrowthAutoResearchEvaluator:
    def evaluate(
        self,
        *,
        policy: GrowthResearchPolicyView,
        champion: GrowthResearchEvidence,
        challenger: GrowthResearchEvidence,
        planned_budget: float | None = None,
        blocked_reason: str | None = None,
        failed_reason: str | None = None,
    ) -> ShadowEvaluationResult:
        champion_cac = self._cac(champion)
        challenger_cac = self._cac(challenger)
        champion_roas = self._roas(champion)
        challenger_roas = self._roas(challenger)

        if blocked_reason:
            return self._result(
                GrowthResearchOutcome.BLOCKED,
                GrowthResearchObjective.NONE,
                [blocked_reason],
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
            )
        if failed_reason:
            return self._result(
                GrowthResearchOutcome.FAILED,
                GrowthResearchObjective.NONE,
                [failed_reason],
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
            )

        protocol_failure = self._protocol_failure(policy, challenger, planned_budget)
        if protocol_failure is not None:
            return self._result(
                GrowthResearchOutcome.FAILED,
                GrowthResearchObjective.NONE,
                [protocol_failure],
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
            )

        objective = self._select_objective(policy, champion, challenger)
        if objective == GrowthResearchObjective.PAID_CAC:
            return self._evaluate_paid_cac(
                policy,
                champion,
                challenger,
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
            )
        if objective == GrowthResearchObjective.PAID_CONVERSION:
            return self._evaluate_conversion(
                policy=policy,
                objective=objective,
                champion=champion,
                challenger=challenger,
                champion_successes=champion.paid_users,
                challenger_successes=challenger.paid_users,
                minimum_successes=policy.min_paid_users_for_decision,
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
                champion_roas=champion_roas,
                challenger_roas=challenger_roas,
                label="paid conversion rate",
            )
        if objective == GrowthResearchObjective.ACTIVATION_CONVERSION:
            return self._evaluate_conversion(
                policy=policy,
                objective=objective,
                champion=champion,
                challenger=challenger,
                champion_successes=champion.activated_users,
                challenger_successes=challenger.activated_users,
                minimum_successes=policy.min_activated_users_for_decision,
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
                champion_roas=champion_roas,
                challenger_roas=challenger_roas,
                label="activation conversion rate",
            )
        if objective == GrowthResearchObjective.SIGNUP_CONVERSION:
            return self._evaluate_conversion(
                policy=policy,
                objective=objective,
                champion=champion,
                challenger=challenger,
                champion_successes=champion.signups,
                challenger_successes=challenger.signups,
                minimum_successes=policy.min_signups_for_decision,
                champion_cac=champion_cac,
                challenger_cac=challenger_cac,
                champion_roas=champion_roas,
                challenger_roas=challenger_roas,
                label="signup conversion rate",
            )

        diagnostic = self._ctr_diagnostic(champion, challenger)
        rationale = [
            "No common decision-grade downstream objective has enough evidence yet.",
            "CTR/click performance is diagnostic only and can never promote a challenger.",
        ]
        if diagnostic is not None:
            rationale.append(diagnostic)
        return self._result(
            GrowthResearchOutcome.INCONCLUSIVE,
            GrowthResearchObjective.NONE,
            rationale,
            champion_cac,
            challenger_cac,
            champion_roas,
            challenger_roas,
        )

    def _select_objective(
        self,
        policy: GrowthResearchPolicyView,
        champion: GrowthResearchEvidence,
        challenger: GrowthResearchEvidence,
    ) -> GrowthResearchObjective:
        paid_minimum = policy.min_paid_users_for_decision
        paid_ready = (
            champion.paid_users >= paid_minimum
            and challenger.paid_users >= paid_minimum
        )
        if paid_ready and champion.spend > 0 and challenger.spend > 0:
            return GrowthResearchObjective.PAID_CAC
        if paid_ready and self._proxy_traffic_ready(policy, champion, challenger):
            return GrowthResearchObjective.PAID_CONVERSION

        activation_minimum = policy.min_activated_users_for_decision
        if (
            champion.activated_users >= activation_minimum
            and challenger.activated_users >= activation_minimum
            and self._proxy_traffic_ready(policy, champion, challenger)
        ):
            return GrowthResearchObjective.ACTIVATION_CONVERSION

        signup_minimum = policy.min_signups_for_decision
        if (
            champion.signups >= signup_minimum
            and challenger.signups >= signup_minimum
            and self._proxy_traffic_ready(policy, champion, challenger)
        ):
            return GrowthResearchObjective.SIGNUP_CONVERSION
        return GrowthResearchObjective.NONE

    def _evaluate_paid_cac(
        self,
        policy: GrowthResearchPolicyView,
        champion: GrowthResearchEvidence,
        challenger: GrowthResearchEvidence,
        champion_cac: float | None,
        challenger_cac: float | None,
        champion_roas: float | None,
        challenger_roas: float | None,
    ) -> ShadowEvaluationResult:
        if champion_cac is None or challenger_cac is None:
            return self._result(
                GrowthResearchOutcome.INCONCLUSIVE,
                GrowthResearchObjective.PAID_CAC,
                ["CAC is not measurable from the supplied paid-user evidence."],
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
            )

        relative_improvement = (champion_cac - challenger_cac) / champion_cac
        confidence = self._paid_rate_confidence(champion, challenger)
        threshold = policy.min_relative_cac_improvement
        rationale = [
            "Using paid-customer CAC because both variants have decision-grade purchase evidence.",
            (
                f"Champion CAC={champion_cac:.4f}; challenger CAC={challenger_cac:.4f}; "
                f"relative improvement={relative_improvement:.1%}."
            ),
            (
                f"Acquisition-rate confidence={confidence:.1%}; "
                f"required={policy.confidence_level:.1%}."
            ),
        ]

        if (
            relative_improvement >= threshold
            and confidence >= policy.confidence_level
        ):
            roas_guardrail = self._roas_guardrail(
                policy,
                champion_roas,
                challenger_roas,
            )
            if roas_guardrail is not None:
                rationale.append(roas_guardrail)
                return self._result(
                    GrowthResearchOutcome.INCONCLUSIVE,
                    GrowthResearchObjective.PAID_CAC,
                    rationale,
                    champion_cac,
                    challenger_cac,
                    champion_roas,
                    challenger_roas,
                    champion_cac,
                    challenger_cac,
                    relative_improvement,
                    confidence,
                )
            rationale.append(
                f"CAC improvement clears the {threshold:.1%} materiality threshold."
            )
            return self._result(
                GrowthResearchOutcome.KEEP,
                GrowthResearchObjective.PAID_CAC,
                rationale,
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
                champion_cac,
                challenger_cac,
                relative_improvement,
                confidence,
            )

        if (
            relative_improvement <= -threshold
            and confidence >= policy.confidence_level
        ):
            rationale.append(
                f"CAC regression clears the {threshold:.1%} discard threshold."
            )
            return self._result(
                GrowthResearchOutcome.DISCARD,
                GrowthResearchObjective.PAID_CAC,
                rationale,
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
                champion_cac,
                challenger_cac,
                relative_improvement,
                confidence,
            )

        rationale.append(
            "The CAC difference is not both material and confidence-qualified; keep gathering evidence."
        )
        return self._result(
            GrowthResearchOutcome.INCONCLUSIVE,
            GrowthResearchObjective.PAID_CAC,
            rationale,
            champion_cac,
            challenger_cac,
            champion_roas,
            challenger_roas,
            champion_cac,
            challenger_cac,
            relative_improvement,
            confidence,
        )

    def _evaluate_conversion(
        self,
        *,
        policy: GrowthResearchPolicyView,
        objective: GrowthResearchObjective,
        champion: GrowthResearchEvidence,
        challenger: GrowthResearchEvidence,
        champion_successes: int,
        challenger_successes: int,
        minimum_successes: int,
        champion_cac: float | None,
        challenger_cac: float | None,
        champion_roas: float | None,
        challenger_roas: float | None,
        label: str,
    ) -> ShadowEvaluationResult:
        if champion_successes > champion.visits or challenger_successes > challenger.visits:
            return self._result(
                GrowthResearchOutcome.FAILED,
                objective,
                [f"Invalid {label} evidence: successes cannot exceed visits."],
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
            )
        if champion_successes < minimum_successes or challenger_successes < minimum_successes:
            return self._result(
                GrowthResearchOutcome.INCONCLUSIVE,
                objective,
                [f"Not enough decision-grade {label} evidence."],
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
            )

        champion_rate = champion_successes / champion.visits
        challenger_rate = challenger_successes / challenger.visits
        relative_improvement = self._relative_rate_improvement(
            champion_rate,
            challenger_rate,
        )
        confidence = self._conversion_confidence(
            champion_successes,
            champion.visits,
            challenger_successes,
            challenger.visits,
        )
        threshold = policy.min_relative_proxy_improvement
        rationale = [
            f"Using {label} because higher-priority purchase economics are not decision-grade for both variants.",
            (
                f"Champion={champion_rate:.2%}; challenger={challenger_rate:.2%}; "
                f"relative improvement={relative_improvement:.1%}."
            ),
            f"Confidence={confidence:.1%}; required={policy.confidence_level:.1%}.",
        ]
        if (
            relative_improvement >= threshold
            and confidence >= policy.confidence_level
        ):
            rationale.append(
                f"Proxy improvement clears the {threshold:.1%} materiality threshold."
            )
            return self._result(
                GrowthResearchOutcome.KEEP,
                objective,
                rationale,
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
                champion_rate,
                challenger_rate,
                relative_improvement,
                confidence,
            )
        if (
            relative_improvement <= -threshold
            and confidence >= policy.confidence_level
        ):
            rationale.append(
                f"Proxy regression clears the {threshold:.1%} discard threshold."
            )
            return self._result(
                GrowthResearchOutcome.DISCARD,
                objective,
                rationale,
                champion_cac,
                challenger_cac,
                champion_roas,
                challenger_roas,
                champion_rate,
                challenger_rate,
                relative_improvement,
                confidence,
            )
        rationale.append(
            "The proxy difference is not both material and confidence-qualified; keep gathering evidence."
        )
        return self._result(
            GrowthResearchOutcome.INCONCLUSIVE,
            objective,
            rationale,
            champion_cac,
            challenger_cac,
            champion_roas,
            challenger_roas,
            champion_rate,
            challenger_rate,
            relative_improvement,
            confidence,
        )

    @staticmethod
    def _protocol_failure(
        policy: GrowthResearchPolicyView,
        challenger: GrowthResearchEvidence,
        planned_budget: float | None,
    ) -> str | None:
        if challenger.duration_hours > policy.max_trial_duration_hours:
            return (
                "Challenger evidence exceeds the comparable trial duration: "
                f"{challenger.duration_hours:.2f}h observed, "
                f"{policy.max_trial_duration_hours:.2f}h allowed."
            )
        if (
            policy.max_shadow_trial_budget > 0
            and challenger.spend > policy.max_shadow_trial_budget
        ):
            return (
                "Challenger evidence exceeds the shadow trial spend cap: "
                f"{challenger.spend:.2f} observed, "
                f"{policy.max_shadow_trial_budget:.2f} allowed."
            )
        if planned_budget is not None and planned_budget > 0 and challenger.spend > planned_budget:
            return (
                "Challenger evidence exceeds its planned test budget: "
                f"{challenger.spend:.2f} observed, {planned_budget:.2f} planned."
            )
        return None

    @staticmethod
    def _proxy_traffic_ready(
        policy: GrowthResearchPolicyView,
        champion: GrowthResearchEvidence,
        challenger: GrowthResearchEvidence,
    ) -> bool:
        minimum = policy.min_visits_for_proxy_decision
        return champion.visits >= minimum and challenger.visits >= minimum

    @staticmethod
    def _paid_rate_confidence(
        champion: GrowthResearchEvidence,
        challenger: GrowthResearchEvidence,
    ) -> float:
        champion_rate = champion.paid_users / champion.spend
        challenger_rate = challenger.paid_users / challenger.spend
        if champion_rate <= 0 or challenger_rate <= 0:
            return 0.0
        log_rate_ratio = log(challenger_rate / champion_rate)
        standard_error = sqrt(
            (1 / champion.paid_users) + (1 / challenger.paid_users)
        )
        if standard_error == 0:
            return 1.0
        z_score = abs(log_rate_ratio) / standard_error
        return round(2 * NormalDist().cdf(z_score) - 1, 6)

    @staticmethod
    def _conversion_confidence(
        champion_successes: int,
        champion_visits: int,
        challenger_successes: int,
        challenger_visits: int,
    ) -> float:
        champion_rate = champion_successes / champion_visits
        challenger_rate = challenger_successes / challenger_visits
        variance = (
            champion_rate * (1 - champion_rate) / champion_visits
            + challenger_rate * (1 - challenger_rate) / challenger_visits
        )
        if variance <= 0:
            return 1.0 if champion_rate != challenger_rate else 0.0
        z_score = abs(challenger_rate - champion_rate) / sqrt(variance)
        return round(2 * NormalDist().cdf(z_score) - 1, 6)

    @staticmethod
    def _relative_rate_improvement(champion_rate: float, challenger_rate: float) -> float:
        if champion_rate == 0:
            return 1.0 if challenger_rate > 0 else 0.0
        return (challenger_rate - champion_rate) / champion_rate

    @staticmethod
    def _roas_guardrail(
        policy: GrowthResearchPolicyView,
        champion_roas: float | None,
        challenger_roas: float | None,
    ) -> str | None:
        if champion_roas is None or challenger_roas is None or champion_roas <= 0:
            return None
        minimum = champion_roas * (1 - policy.max_relative_roas_regression)
        if challenger_roas < minimum:
            return (
                "CAC improved, but challenger ROAS regressed beyond the allowed business "
                f"guardrail ({challenger_roas:.3f} < {minimum:.3f})."
            )
        return None

    @staticmethod
    def _ctr_diagnostic(
        champion: GrowthResearchEvidence,
        challenger: GrowthResearchEvidence,
    ) -> str | None:
        if champion.impressions <= 0 or challenger.impressions <= 0:
            return None
        champion_ctr = champion.clicks / champion.impressions
        challenger_ctr = challenger.clicks / challenger.impressions
        return (
            f"Diagnostic CTR: champion={champion_ctr:.2%}; challenger={challenger_ctr:.2%}."
        )

    @staticmethod
    def _cac(evidence: GrowthResearchEvidence) -> float | None:
        if evidence.paid_users <= 0:
            return None
        return round(evidence.spend / evidence.paid_users, 4)

    @staticmethod
    def _roas(evidence: GrowthResearchEvidence) -> float | None:
        if evidence.spend <= 0:
            return None
        return round(evidence.revenue / evidence.spend, 4)

    @staticmethod
    def _result(
        outcome: GrowthResearchOutcome,
        objective: GrowthResearchObjective,
        rationale: list[str],
        champion_cac: float | None,
        challenger_cac: float | None,
        champion_roas: float | None,
        challenger_roas: float | None,
        champion_metric_value: float | None = None,
        challenger_metric_value: float | None = None,
        relative_improvement: float | None = None,
        confidence: float | None = None,
    ) -> ShadowEvaluationResult:
        return ShadowEvaluationResult(
            outcome=outcome,
            objective=objective,
            rationale=rationale,
            champion_cac=champion_cac,
            challenger_cac=challenger_cac,
            champion_roas=champion_roas,
            challenger_roas=challenger_roas,
            champion_metric_value=champion_metric_value,
            challenger_metric_value=challenger_metric_value,
            relative_improvement=relative_improvement,
            confidence=confidence,
        )


growth_autoresearch_evaluator = GrowthAutoResearchEvaluator()
