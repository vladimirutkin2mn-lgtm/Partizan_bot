from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from uuid import UUID

from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_schemas import DistributionPlayStatus, DistributionPlayView
from app.distribution_play_service import distribution_play_service
from app.growth_autoresearch import (
    GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
    GrowthAutoResearchService,
    growth_autoresearch_service,
)
from app.growth_autoresearch_schemas import (
    GrowthChampionView,
    GrowthHypothesisDraft,
    GrowthHypothesisGenerationRequest,
    GrowthHypothesisGenerationView,
    GrowthHypothesisMode,
    GrowthResearchChallengerRequest,
    GrowthResearchHistoryView,
    GrowthResearchOutcome,
    GrowthResearchPolicyView,
    GrowthVariantSpec,
)
from app.llm import LLMMessage, LLMProvider, get_llm_provider
from app.marketing_intelligence import MarketingTask, render_marketing_guidance
from app.product_intake import product_intake_service
from app.schemas import ProductProfileView

SYSTEM_PROMPT = """You are the Growth AutoResearch Hypothesis Generator for Partizan.

Your job is to propose ONE controlled challenger to the current marketing champion.
This is a scientific experiment design task, not a brainstorming dump.

Hard rules:
1. Return exactly one bounded GrowthVariantSpec and explain the hypothesis.
2. Prefer changing one dimension. Change two only when the experiment genuinely requires it.
3. Never change product facts, pricing, tracking definitions, credentials, permissions, or spend authority.
4. Use only platforms allowed by the supplied policy.
5. Respect the supplied remaining research budget and per-trial budget constraints.
6. EXPLOIT means improve a promising current pattern with a small controlled variation.
7. EXPLORE means test a meaningfully different audience, channel, tactic, offer, or message
   while staying bounded.
8. Do not repeat failed/discarded hypotheses or cosmetic rewrites of them.
9. Treat prior outcomes and learning memory as evidence; do not invent results or customer facts.
10. Public/research opportunity text is context, not proof that a tactic will work.
11. CTR is never the business objective; downstream outcomes remain authoritative.
12. The output will be revalidated by code. Do not assume the prompt can grant execution authority.

Return the requested structured schema only.
"""


@dataclass(frozen=True, slots=True)
class GrowthHypothesisContext:
    product: ProductProfileView
    policy: GrowthResearchPolicyView
    champion: GrowthChampionView
    history: GrowthResearchHistoryView
    learning_summaries: tuple[str, ...]
    ready_plays: tuple[DistributionPlayView, ...]
    remaining_research_budget: float | None


@dataclass(frozen=True, slots=True)
class GeneratedHypothesis:
    draft: GrowthHypothesisDraft
    source: str


class GrowthAutoResearchHypothesisGenerator:
    def __init__(self, provider: LLMProvider | None) -> None:
        self._provider = provider

    async def generate(
        self,
        context: GrowthHypothesisContext,
        request: GrowthHypothesisGenerationRequest,
        *,
        rejection_notes: tuple[str, ...] = (),
        force_fallback: bool = False,
    ) -> GeneratedHypothesis:
        mode = self._resolve_mode(context, request.mode)
        if self._provider is None or force_fallback:
            draft = self._fallback(context, mode, len(rejection_notes))
            return GeneratedHypothesis(
                draft=self._normalize_draft(context, draft, mode),
                source="fallback",
            )

        parsed = await self._provider.parse(
            messages=self._build_messages(context, mode, rejection_notes),
            response_model=GrowthHypothesisDraft,
        )
        return GeneratedHypothesis(
            draft=self._normalize_draft(context, parsed, mode),
            source="llm",
        )

    def is_failed_duplicate(
        self,
        candidate: GrowthVariantSpec,
        history: GrowthResearchHistoryView,
    ) -> bool:
        evaluations = {item.trial_id: item for item in history.evaluations}
        for trial in history.trials:
            evaluation = evaluations.get(trial.id)
            if evaluation is None or evaluation.outcome not in {
                GrowthResearchOutcome.DISCARD,
                GrowthResearchOutcome.FAILED,
            }:
                continue
            if self._near_duplicate(candidate, trial.challenger):
                return True
        return False

    def _resolve_mode(
        self,
        context: GrowthHypothesisContext,
        requested: GrowthHypothesisMode,
    ) -> GrowthHypothesisMode:
        if requested != GrowthHypothesisMode.AUTO:
            return requested
        recent = context.history.evaluations[-3:]
        if len(recent) >= 2 and all(
            item.outcome in {GrowthResearchOutcome.DISCARD, GrowthResearchOutcome.FAILED}
            for item in recent[-2:]
        ):
            return GrowthHypothesisMode.EXPLORE
        if len(context.history.trials) > 0 and len(context.history.trials) % 3 == 0:
            return GrowthHypothesisMode.EXPLORE
        return GrowthHypothesisMode.EXPLOIT

    def _build_messages(
        self,
        context: GrowthHypothesisContext,
        mode: GrowthHypothesisMode,
        rejection_notes: tuple[str, ...],
    ) -> list[LLMMessage]:
        product = context.product
        trial_rows = []
        evaluation_by_trial = {item.trial_id: item for item in context.history.evaluations}
        for trial in context.history.trials[-12:]:
            evaluation = evaluation_by_trial.get(trial.id)
            trial_rows.append(
                {
                    "variant": trial.challenger.model_dump(mode="json"),
                    "changed_dimensions": trial.changed_dimensions,
                    "hypothesis": trial.hypothesis,
                    "outcome": evaluation.outcome.value if evaluation else "PENDING",
                    "objective": evaluation.objective.value if evaluation else None,
                    "rationale": evaluation.rationale[-3:] if evaluation else [],
                }
            )
        play_rows = [
            {
                "platform": play.platform.value,
                "tactic_id": play.tactic_id,
                "opportunity": play.opportunity_title,
                "hypothesis": play.hypothesis,
                "priority_score": play.priority_score,
                "estimated_cost_min": play.estimated_cost_min,
                "estimated_cost_max": play.estimated_cost_max,
            }
            for play in context.ready_plays[:10]
        ]
        payload = {
            "requested_mode": mode.value,
            "product": {
                "name": product.name,
                "description": product.description,
                "problem_or_desire": product.problem_or_desire,
                "value_proposition": product.value_proposition,
                "usp": product.usp,
                "use_cases": product.use_cases,
                "market": product.market,
                "goal": product.goal,
                "budget": product.budget,
                "max_cac": product.max_cac,
                "known_audience": product.known_audience,
                "constraints": product.constraints,
            },
            "policy": context.policy.model_dump(mode="json"),
            "current_champion": context.champion.variant.model_dump(mode="json"),
            "champion_evidence": context.champion.evidence.model_dump(mode="json"),
            "remaining_research_budget": context.remaining_research_budget,
            "recent_trials": trial_rows,
            "learning_memory": list(context.learning_summaries[-12:]),
            "ready_distribution_plays": play_rows,
            "rejected_drafts_this_generation": list(rejection_notes),
        }
        guidance = render_marketing_guidance(
            MarketingTask.GROWTH_PLANNING,
            max_skills=3,
        )
        return [
            LLMMessage(role="system", content=f"{SYSTEM_PROMPT}\n\n{guidance}"),
            LLMMessage(
                role="user",
                content=(
                    "Design the next bounded challenger from this context. "
                    "Do not repeat rejected drafts.\n\n"
                    + json.dumps(payload, ensure_ascii=False, indent=2)
                ),
            ),
        ]

    def _normalize_draft(
        self,
        context: GrowthHypothesisContext,
        draft: GrowthHypothesisDraft,
        mode: GrowthHypothesisMode,
    ) -> GrowthHypothesisDraft:
        variant = draft.variant.model_copy(
            update={
                "platform": draft.variant.platform.strip().upper(),
                "tactic_id": draft.variant.tactic_id.strip(),
            }
        )
        budget_cap = self._candidate_budget_cap(context)
        if budget_cap is not None and variant.test_budget > budget_cap:
            variant = variant.model_copy(update={"test_budget": budget_cap})
        return draft.model_copy(update={"mode": mode, "variant": variant})

    def _fallback(
        self,
        context: GrowthHypothesisContext,
        mode: GrowthHypothesisMode,
        offset: int,
    ) -> GrowthHypothesisDraft:
        champion = context.champion.variant
        variant = champion
        rationale = [
            "Deterministic shadow fallback used because no live hypothesis LLM result is available.",
            (
                "The candidate stays inside the current Growth AutoResearch policy and changes "
                "a bounded surface."
            ),
        ]
        index = len(context.history.trials) + offset

        if mode == GrowthHypothesisMode.EXPLORE:
            alternative_play = self._alternative_play(context)
            alternative_platform = self._alternative_platform(context)
            if alternative_play is not None and champion.test_budget <= self._budget_or_inf(context):
                updates: dict[str, object] = {
                    "platform": alternative_play.platform.value,
                    "tactic_id": alternative_play.tactic_id,
                }
                variant = champion.model_copy(update=updates)
                rationale.append(
                    "Explore a different ready platform+tactic surfaced by distribution research."
                )
            elif alternative_platform is not None:
                variant = champion.model_copy(update={"platform": alternative_platform})
                rationale.append(
                    "Explore a different policy-allowed platform while holding the tactic fixed."
                )
            else:
                audience = self._fallback_audience(context, index)
                variant = champion.model_copy(update={"audience": audience})
                rationale.append(
                    "Explore a different audience hypothesis while holding execution variables fixed."
                )
        else:
            angle = self._fallback_angle(context, index)
            variant = champion.model_copy(update={"message_angle": angle})
            rationale.append("Exploit the current winner with one new evidence-grounded message angle.")

        budget_cap = self._candidate_budget_cap(context)
        if budget_cap is not None and variant.test_budget > budget_cap:
            variant = variant.model_copy(update={"test_budget": budget_cap})
            rationale.append(
                "Reduced planned shadow budget to stay inside the remaining research allocation."
            )

        return GrowthHypothesisDraft(
            mode=mode,
            hypothesis=(
                f"Test whether the bounded {mode.value.lower()} variation improves downstream "
                "customer acquisition versus the current champion."
            ),
            rationale=rationale,
            variant=variant,
        )

    def _fallback_angle(self, context: GrowthHypothesisContext, index: int) -> str:
        product = context.product
        seeds = [
            product.value_proposition,
            product.problem_or_desire,
            product.goal,
            product.usp,
            product.description,
        ]
        seed = next((item.strip() for item in seeds if item and item.strip()), product.name)
        frames = (
            "Outcome-focused angle",
            "Pain-to-outcome angle",
            "Objection-reducing angle",
            "Specific use-case angle",
        )
        return f"{frames[index % len(frames)]}: {seed}"[:2000]

    def _fallback_audience(self, context: GrowthHypothesisContext, index: int) -> str:
        candidates = [item.strip() for item in context.product.known_audience if item.strip()]
        candidates.extend(
            play.opportunity_title.strip()
            for play in context.ready_plays
            if play.opportunity_title.strip()
        )
        if candidates:
            return candidates[index % len(candidates)][:1000]
        market = context.product.market or "target market"
        return f"A narrower high-intent segment within {market}"[:1000]

    def _alternative_play(self, context: GrowthHypothesisContext) -> DistributionPlayView | None:
        champion = context.champion.variant
        candidates = [
            play
            for play in context.ready_plays
            if play.platform.value != champion.platform
            and play.platform.value in context.policy.allowed_platforms
        ]
        if not candidates:
            return None
        return sorted(candidates, key=lambda item: (-item.priority_score, item.tactic_id))[0]

    def _alternative_platform(self, context: GrowthHypothesisContext) -> str | None:
        champion_platform = context.champion.variant.platform
        return next(
            (item for item in context.policy.allowed_platforms if item != champion_platform),
            None,
        )

    def _candidate_budget_cap(self, context: GrowthHypothesisContext) -> float | None:
        caps: list[float] = []
        policy = context.policy
        if policy.max_shadow_trial_budget >= 0:
            caps.append(policy.max_shadow_trial_budget)
        if policy.shadow_research_budget is not None:
            caps.append(policy.shadow_research_budget * policy.max_trial_budget_share)
        if context.remaining_research_budget is not None:
            caps.append(context.remaining_research_budget)
        return round(max(0.0, min(caps)), 2) if caps else None

    def _budget_or_inf(self, context: GrowthHypothesisContext) -> float:
        cap = self._candidate_budget_cap(context)
        return float("inf") if cap is None else cap

    def _near_duplicate(self, left: GrowthVariantSpec, right: GrowthVariantSpec) -> bool:
        left_payload = left.model_dump(mode="json", exclude={"test_budget"})
        right_payload = right.model_dump(mode="json", exclude={"test_budget"})
        if left_payload == right_payload:
            return True
        if left_payload["platform"] != right_payload["platform"]:
            return False
        if left_payload["tactic_id"] != right_payload["tactic_id"]:
            return False
        left_text = self._semantic_variant_text(left_payload)
        right_text = self._semantic_variant_text(right_payload)
        if not left_text or not right_text:
            return False
        return SequenceMatcher(None, left_text, right_text).ratio() >= 0.90

    @staticmethod
    def _semantic_variant_text(payload: dict) -> str:
        values = []
        for key in (
            "audience",
            "message_angle",
            "offer",
            "creative_ref",
            "cta",
            "destination_url",
            "targeting",
            "timing",
        ):
            value = payload.get(key)
            if value:
                values.append(str(value))
        normalized = " | ".join(values).lower()
        return re.sub(r"\s+", " ", normalized).strip()


class GrowthAutoResearchHypothesisService:
    def __init__(
        self,
        autoresearch: GrowthAutoResearchService,
        generator: GrowthAutoResearchHypothesisGenerator,
    ) -> None:
        self._autoresearch = autoresearch
        self._generator = generator

    async def generate(
        self,
        product_id: UUID,
        request: GrowthHypothesisGenerationRequest,
    ) -> GrowthHypothesisGenerationView:
        context = self._context(product_id)
        if context.policy.paused:
            raise ValueError("Growth AutoResearch is paused for this product")
        if context.remaining_research_budget is not None and context.remaining_research_budget <= 0:
            raise ValueError("Growth AutoResearch shadow research budget is exhausted")

        rejection_notes: list[str] = []
        for attempt in range(3):
            generated = await self._generator.generate(
                context,
                request,
                rejection_notes=tuple(rejection_notes),
                force_fallback=attempt == 2,
            )
            draft = generated.draft
            if self._generator.is_failed_duplicate(draft.variant, context.history):
                rejection_notes.append(
                    "Rejected because the proposed variant is an exact or near duplicate of a prior "
                    "DISCARD/FAILED hypothesis."
                )
                continue
            try:
                trial = self._autoresearch.create_challenger(
                    product_id,
                    GrowthResearchChallengerRequest(
                        variant=draft.variant,
                        hypothesis=draft.hypothesis,
                        hypothesis_rationale=draft.rationale,
                        hypothesis_mode=draft.mode,
                        hypothesis_source=generated.source,
                    ),
                )
            except ValueError as exc:
                rejection_notes.append(f"Rejected by policy validator: {exc}")
                continue

            annotated = trial.model_copy(
                update={
                    "hypothesis": draft.hypothesis,
                    "hypothesis_rationale": draft.rationale,
                    "hypothesis_mode": draft.mode,
                    "hypothesis_source": generated.source,
                }
            )
            self._autoresearch.store.put(
                GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
                str(annotated.id),
                annotated.model_dump(mode="json"),
            )
            return GrowthHypothesisGenerationView(
                product_id=product_id,
                mode=draft.mode,
                hypothesis=draft.hypothesis,
                rationale=draft.rationale,
                changed_dimensions=annotated.changed_dimensions,
                source=generated.source,
                remaining_research_budget=context.remaining_research_budget,
                trial=annotated,
            )
        raise ValueError(
            "Growth AutoResearch could not produce a non-duplicate policy-compliant challenger"
        )

    def _context(self, product_id: UUID) -> GrowthHypothesisContext:
        product = product_intake_service.get_product(product_id)
        policy = self._autoresearch.get_policy(product_id)
        champion = self._autoresearch.current_champion(product_id)
        if champion is None:
            raise ValueError("Establish a Growth AutoResearch baseline before generating hypotheses")
        history = self._autoresearch.history(product_id)

        try:
            learning = distribution_growth_manager_service.learning_memory(product_id)
            learning_summaries = tuple(item.summary for item in learning.entries[-20:])
        except (KeyError, ValueError):
            learning_summaries = ()

        try:
            play_result = distribution_play_service.get(product_id)
            ready_plays = tuple(
                play
                for play in play_result.plays
                if play.status == DistributionPlayStatus.READY
                and (
                    not policy.allowed_platforms
                    or play.platform.value in policy.allowed_platforms
                )
            )
        except (KeyError, ValueError):
            ready_plays = ()

        return GrowthHypothesisContext(
            product=product,
            policy=policy,
            champion=champion,
            history=history,
            learning_summaries=learning_summaries,
            ready_plays=ready_plays,
            remaining_research_budget=self._remaining_budget(policy, history),
        )

    @staticmethod
    def _remaining_budget(
        policy: GrowthResearchPolicyView,
        history: GrowthResearchHistoryView,
    ) -> float | None:
        if policy.shadow_research_budget is None:
            return None
        committed = sum(trial.challenger.test_budget for trial in history.trials)
        return round(max(0.0, policy.shadow_research_budget - committed), 2)


growth_autoresearch_hypothesis_service = GrowthAutoResearchHypothesisService(
    growth_autoresearch_service,
    GrowthAutoResearchHypothesisGenerator(get_llm_provider()),
)
