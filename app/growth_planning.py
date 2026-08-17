from dataclasses import dataclass

from app.distribution_play_schemas import DistributionPlayView, DistributionTacticClass
from app.marketing_intelligence import MarketingTask, skill_router

MAX_PLANNING_ADJUSTMENT = 10.0
GROWTH_PLANNING_SKILLS = tuple(
    pack.name for pack in skill_router.select(MarketingTask.GROWTH_PLANNING)
)


@dataclass(frozen=True, slots=True)
class GrowthPlanningAssessment:
    adjustment: float
    feasible: bool
    rationale: tuple[str, ...]
    skills: tuple[str, ...] = GROWTH_PLANNING_SKILLS


class GrowthPlanningEngine:
    def assess(
        self,
        play: DistributionPlayView,
        *,
        budget_remaining: float | None,
        research_signals: dict | None = None,
    ) -> GrowthPlanningAssessment:
        signals = research_signals or {}
        reasons = [
            "Growth planning skills: " + ", ".join(GROWTH_PLANNING_SKILLS) + "."
        ]

        if budget_remaining is not None and play.estimated_cost_min > budget_remaining:
            reasons.append(
                "Budget feasibility: estimated minimum cost "
                f"{play.estimated_cost_min:.2f} exceeds remaining budget "
                f"{budget_remaining:.2f}; do not sequence this play yet."
            )
            return GrowthPlanningAssessment(
                adjustment=-MAX_PLANNING_ADJUSTMENT,
                feasible=False,
                rationale=tuple(reasons),
            )

        adjustment = 0.0
        evidence_adjustment, evidence_reason = self._evidence_adjustment(signals)
        adjustment += evidence_adjustment
        reasons.append(evidence_reason)

        speed_adjustment, speed_reason = self._speed_adjustment(play.time_to_signal_days)
        adjustment += speed_adjustment
        reasons.append(speed_reason)

        effort_adjustment, effort_reason = self._effort_adjustment(play.effort_hours)
        adjustment += effort_adjustment
        reasons.append(effort_reason)

        budget_adjustment, budget_reason = self._budget_adjustment(
            play,
            budget_remaining,
        )
        adjustment += budget_adjustment
        if budget_reason:
            reasons.append(budget_reason)

        compounding_adjustment, compounding_reason = self._compounding_adjustment(play)
        adjustment += compounding_adjustment
        if compounding_reason:
            reasons.append(compounding_reason)

        bounded = round(
            max(-MAX_PLANNING_ADJUSTMENT, min(MAX_PLANNING_ADJUSTMENT, adjustment)),
            1,
        )
        reasons.append(
            f"Growth planning adjustment={bounded:+.1f}; observed experiment economics remain authoritative."
        )
        return GrowthPlanningAssessment(
            adjustment=bounded,
            feasible=True,
            rationale=tuple(reasons),
        )

    def _evidence_adjustment(self, research_signals: dict) -> tuple[float, str]:
        confidence = str(research_signals.get("confidence", "UNKNOWN")).upper()
        independent = self._safe_int(
            research_signals.get("independent_evidence_count"),
        )
        demand = self._safe_int(research_signals.get("demand_intent_hits"))
        commercial = self._safe_int(research_signals.get("commercial_intent_hits"))

        confidence_adjustment = {
            "HIGH": 4.0,
            "MEDIUM": 2.0,
            "LOW": -1.0,
        }.get(confidence, 0.0)
        repeated_adjustment = 1.0 if independent >= 2 else 0.0
        intent_adjustment = 1.0 if demand > 0 or commercial > 0 else 0.0
        total = confidence_adjustment + repeated_adjustment + intent_adjustment
        return total, (
            "Audience evidence: "
            f"confidence={confidence}, independent_sources={independent}, "
            f"demand_intent_hits={demand}, commercial_intent_hits={commercial}; "
            f"planning impact={total:+.1f}."
        )

    def _speed_adjustment(self, days: int) -> tuple[float, str]:
        if days <= 3:
            value = 4.0
        elif days <= 7:
            value = 2.0
        elif days <= 14:
            value = 1.0
        elif days > 30:
            value = -3.0
        elif days > 21:
            value = -2.0
        else:
            value = 0.0
        return value, f"Speed to signal: {days} days; planning impact={value:+.1f}."

    def _effort_adjustment(self, hours: float) -> tuple[float, str]:
        if hours <= 1:
            value = 2.0
        elif hours <= 3:
            value = 1.0
        elif hours >= 12:
            value = -3.0
        elif hours >= 8:
            value = -2.0
        else:
            value = 0.0
        return value, f"Execution effort: {hours:.1f}h; planning impact={value:+.1f}."

    def _budget_adjustment(
        self,
        play: DistributionPlayView,
        budget_remaining: float | None,
    ) -> tuple[float, str | None]:
        if budget_remaining is None or budget_remaining <= 0:
            return 0.0, None
        if play.estimated_cost_max <= max(5.0, budget_remaining * 0.25):
            return 2.0, (
                "Budget fit: the full estimated test fits inside roughly one quarter of "
                "remaining budget; planning impact=+2.0."
            )
        if play.estimated_cost_min > 0 and play.estimated_cost_max > budget_remaining * 0.75:
            return -3.0, (
                "Budget fit: the estimated test can consume more than three quarters of "
                "remaining budget; planning impact=-3.0."
            )
        return 0.0, "Budget fit: estimated test size is neutral for current remaining budget."

    def _compounding_adjustment(
        self,
        play: DistributionPlayView,
    ) -> tuple[float, str | None]:
        if play.tactic_class in {
            DistributionTacticClass.COMMUNITY,
            DistributionTacticClass.OWNED_ORGANIC,
            DistributionTacticClass.OUTREACH,
        }:
            return 1.0, (
                "Channel balance: community, owned-organic or partner/outreach work can compound "
                "beyond one paid impression; planning impact=+1.0."
            )
        return 0.0, None

    def _safe_int(self, value) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


growth_planning_engine = GrowthPlanningEngine()
