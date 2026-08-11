from dataclasses import dataclass

from app.analytics_schemas import ExperimentAnalyticsView, ProductAnalyticsView
from app.growth_manager_schemas import NextHypothesisView
from app.schemas import GrowthPlayView, ProductProfileView

POLICY_VERSION = "growth-policy-v1"
MIN_SCALE_PAID_USERS = 3


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: str
    rationale: list[str]
    budget_remaining: float | None
    recommended_budget_increment: float
    next_hypothesis: NextHypothesisView


class GrowthPolicy:
    def evaluate(
        self,
        product: ProductProfileView,
        play: GrowthPlayView,
        analytics: ExperimentAnalyticsView,
        product_analytics: ProductAnalyticsView,
    ) -> PolicyDecision:
        metrics = analytics.metrics
        target_cac = product.max_cac
        budget_remaining = self._budget_remaining(product, product_analytics)

        if budget_remaining is not None and budget_remaining <= 0 and metrics.spend > 0:
            action = "STOP"
            rationale = [
                "Product marketing budget is exhausted; no additional spend is allowed.",
                self._metric_summary(metrics, target_cac),
            ]
        elif target_cac is not None:
            action, rationale = self._with_cac_target(
                metrics=metrics,
                target_cac=target_cac,
                play=play,
            )
        else:
            action, rationale = self._without_cac_target(
                metrics=metrics,
                play=play,
            )

        increment = self._recommended_increment(
            action=action,
            play=play,
            current_spend=metrics.spend,
            budget_remaining=budget_remaining,
        )
        next_hypothesis = self._next_hypothesis(
            action=action,
            play=play,
            analytics=analytics,
            target_cac=target_cac,
            recommended_increment=increment,
        )
        return PolicyDecision(
            action=action,
            rationale=rationale,
            budget_remaining=budget_remaining,
            recommended_budget_increment=increment,
            next_hypothesis=next_hypothesis,
        )

    def _with_cac_target(
        self,
        metrics,
        target_cac: float,
        play: GrowthPlayView,
    ) -> tuple[str, list[str]]:
        if metrics.paid_users >= MIN_SCALE_PAID_USERS and metrics.cac is not None:
            ratio = metrics.cac / target_cac if target_cac else float("inf")
            if ratio <= 0.8:
                return "SCALE", [
                    (
                        f"Observed CAC {metrics.cac:.2f} is at least 20% below "
                        f"target {target_cac:.2f}."
                    ),
                    (
                        f"Signal has {metrics.paid_users} paid users, "
                        "meeting the scale threshold."
                    ),
                ]
            if ratio <= 1.1:
                return "CONTINUE", [
                    (
                        f"Observed CAC {metrics.cac:.2f} is close to "
                        f"target {target_cac:.2f}."
                    ),
                    "Keep collecting signal before increasing spend.",
                ]
            if ratio <= 1.5:
                return "MODIFY", [
                    (
                        f"Observed CAC {metrics.cac:.2f} is above target "
                        f"{target_cac:.2f} but not catastrophic."
                    ),
                    (
                        "The experiment has conversions, so optimize the play "
                        "before abandoning the ICP/channel."
                    ),
                ]
            return "STOP", [
                (
                    f"Observed CAC {metrics.cac:.2f} is more than 50% above "
                    f"target {target_cac:.2f}."
                ),
                (
                    f"Signal already includes {metrics.paid_users} paid users, "
                    "so the poor unit economics are meaningful."
                ),
            ]

        if metrics.paid_users > 0 and metrics.cac is not None:
            if metrics.cac <= target_cac:
                return "CONTINUE", [
                    (
                        f"Early CAC {metrics.cac:.2f} is within target "
                        f"{target_cac:.2f}."
                    ),
                    (
                        f"Only {metrics.paid_users} paid users observed; "
                        "collect more signal before scaling."
                    ),
                ]
            if (
                metrics.cac > target_cac * 1.5
                and metrics.spend >= max(25, target_cac * 2)
            ):
                return "STOP", [
                    (
                        f"Early CAC {metrics.cac:.2f} materially exceeds target "
                        f"{target_cac:.2f}."
                    ),
                    (
                        f"Spend {metrics.spend:.2f} is already large enough "
                        "to enforce the loss guardrail."
                    ),
                ]
            return "MODIFY", [
                (
                    f"The experiment converts, but CAC {metrics.cac:.2f} is above "
                    f"target {target_cac:.2f}."
                ),
                "Improve cost, offer or conversion before collecting a larger sample.",
            ]

        stop_threshold = max(play.estimated_cost_max, target_cac * 3, 25)
        if metrics.spend >= stop_threshold:
            return "STOP", [
                f"No paid users after {metrics.spend:.2f} spend.",
                f"Loss guardrail {stop_threshold:.2f} has been reached.",
            ]
        if metrics.visits >= 20 and metrics.signups == 0:
            return "MODIFY", [
                f"There are {metrics.visits} visits but no signups.",
                "The top-of-funnel message, CTA or landing experience needs a change.",
            ]
        if metrics.signups >= 5 and metrics.paid_users == 0:
            return "MODIFY", [
                f"There are {metrics.signups} signups but no paid users.",
                "The activation, offer or paywall is the likely bottleneck.",
            ]
        return "CONTINUE", [
            "There is not enough conversion signal to make a stop/scale decision yet.",
            self._metric_summary(metrics, target_cac),
        ]

    def _without_cac_target(
        self,
        metrics,
        play: GrowthPlayView,
    ) -> tuple[str, list[str]]:
        if (
            metrics.paid_users >= MIN_SCALE_PAID_USERS
            and metrics.roas is not None
            and metrics.roas >= 1
        ):
            return "SCALE", [
                f"ROAS is {metrics.roas:.3f} with {metrics.paid_users} paid users.",
                (
                    "No CAC target is configured, so positive observed return "
                    "is used as the scale guardrail."
                ),
            ]
        if metrics.paid_users > 0:
            return "CONTINUE", [
                f"The experiment has {metrics.paid_users} paid users.",
                (
                    "No CAC target is configured; collect more signal before "
                    "changing allocation."
                ),
            ]
        stop_threshold = max(play.estimated_cost_max, 100)
        if metrics.spend >= stop_threshold:
            return "STOP", [
                f"No paid users after {metrics.spend:.2f} spend.",
                f"Fallback loss guardrail {stop_threshold:.2f} has been reached.",
            ]
        if metrics.visits >= 20:
            return "MODIFY", [
                f"The experiment has {metrics.visits} visits and no paid users.",
                "Change the message/offer before spending materially more.",
            ]
        return "CONTINUE", [
            "Insufficient signal and no CAC target configured.",
            self._metric_summary(metrics, None),
        ]

    def _budget_remaining(
        self,
        product: ProductProfileView,
        product_analytics: ProductAnalyticsView,
    ) -> float | None:
        if product.budget is None:
            return None
        return round(
            max(0.0, product.budget - product_analytics.total_spend),
            2,
        )

    def _recommended_increment(
        self,
        action: str,
        play: GrowthPlayView,
        current_spend: float,
        budget_remaining: float | None,
    ) -> float:
        if action not in {"SCALE", "CONTINUE"}:
            return 0.0
        if action == "SCALE":
            desired = max(current_spend, play.estimated_cost_max, 25)
        else:
            desired = max(0.0, play.estimated_cost_max - current_spend)
        if budget_remaining is not None:
            desired = min(desired, budget_remaining)
        return round(max(0.0, desired), 2)

    def _next_hypothesis(
        self,
        action: str,
        play: GrowthPlayView,
        analytics: ExperimentAnalyticsView,
        target_cac: float | None,
        recommended_increment: float,
    ) -> NextHypothesisView:
        metrics = analytics.metrics
        if action == "SCALE":
            if target_cac is not None:
                target_text = f"while CAC stays ≤ {target_cac:.2f}"
            else:
                target_text = "while economics remain positive"
            return NextHypothesisView(
                title="Scale the winning play",
                change=(
                    f"Repeat `{play.template_id}` on the same source type with "
                    f"an additional budget cap of {recommended_increment:.2f}."
                ),
                success_condition=target_text,
            )
        if action == "CONTINUE":
            return NextHypothesisView(
                title="Collect a stronger signal",
                change=(
                    f"Keep the current play unchanged until at least "
                    f"{MIN_SCALE_PAID_USERS} paid users or the current test "
                    "guardrail is reached."
                ),
                success_condition=(
                    f"Reach {MIN_SCALE_PAID_USERS} paid users with stable CAC/quality."
                ),
            )
        if action == "MODIFY":
            if metrics.visits >= 20 and metrics.signups == 0:
                change = (
                    "Test a new hook/CTA or landing message while keeping the "
                    "ICP and channel fixed."
                )
            elif metrics.signups >= 5 and metrics.paid_users == 0:
                change = (
                    "Test a stronger offer, activation path or paywall while "
                    "keeping acquisition traffic fixed."
                )
            elif (
                target_cac is not None
                and metrics.cac is not None
                and metrics.cac > target_cac
            ):
                change = (
                    "Keep the ICP but test a lower-cost offer or partnership "
                    "structure on the same source type."
                )
            else:
                change = (
                    "Change one major variable (hook or offer) while keeping "
                    "the ICP/channel constant."
                )
            return NextHypothesisView(
                title="Modify one bottleneck",
                change=change,
                success_condition=(
                    "Improve the bottleneck metric without degrading downstream "
                    "paid conversion."
                ),
            )
        return NextHypothesisView(
            title="Replace the losing channel/tactic",
            change=(
                f"Retire `{play.template_id}` for this opportunity and test the "
                "same ICP through a different source type."
            ),
            success_condition=(
                "Produce a lower CAC or a measurable conversion signal within "
                "the next test guardrail."
            ),
        )

    def _metric_summary(self, metrics, target_cac: float | None) -> str:
        target = f", target CAC={target_cac:.2f}" if target_cac is not None else ""
        return (
            f"Observed: spend={metrics.spend:.2f}, visits={metrics.visits}, "
            f"signups={metrics.signups}, paid={metrics.paid_users}, "
            f"CAC={metrics.cac}{target}."
        )
