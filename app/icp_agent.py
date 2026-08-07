import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.llm import LLMMessage, LLMProvider


class ICPDimensionScores(BaseModel):
    pain_intensity: int = Field(ge=1, le=10)
    purchase_intent: int = Field(ge=1, le=10)
    willingness_to_pay: int = Field(ge=1, le=10)
    ease_of_targeting: int = Field(ge=1, le=10)
    market_size: int = Field(ge=1, le=10)
    competitive_headroom: int = Field(ge=1, le=10)
    speed_of_validation: int = Field(ge=1, le=10)


class ICPCandidate(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=10)
    pain: str
    desired_outcome: str
    trigger: str
    willingness_to_pay_hypothesis: str
    alternatives: list[str] = Field(default_factory=list)
    message_hook: str
    dimensions: ICPDimensionScores
    rationale: list[str] = Field(default_factory=list)


class ICPGeneration(BaseModel):
    candidates: list[ICPCandidate] = Field(min_length=12, max_length=30)


@dataclass(frozen=True, slots=True)
class RankedICPCandidate:
    candidate: ICPCandidate
    total_score: float
    score_explanation: str
    duplicate_of: str | None = None


@dataclass(frozen=True, slots=True)
class ICPRankingResult:
    ranked: list[RankedICPCandidate]
    duplicate_clusters: dict[str, list[str]]


SCORE_WEIGHTS = {
    "pain_intensity": 0.20,
    "purchase_intent": 0.20,
    "willingness_to_pay": 0.15,
    "ease_of_targeting": 0.15,
    "market_size": 0.10,
    "competitive_headroom": 0.10,
    "speed_of_validation": 0.10,
}

SYSTEM_PROMPT = """You are the ICP Agent for Partizan Bot, an AI growth operator.

Given a founder-confirmed ProductProfile, generate 12-20 meaningfully distinct target
customer segments that can later be mapped to concrete acquisition channels.

Rules:
1. Segment by pain/desire, trigger, context, behavior, intent and alternatives. Do not rely
   primarily on broad demographics such as "men 25-40".
2. Every segment must be specific enough to search for communities, creators, queries or
   other distribution points later.
3. Separate current urgent demand from curiosity, repeat use and switcher segments.
4. Do not invent product capabilities that are absent from the ProductProfile.
5. Treat every segment as a hypothesis, not a proven market fact.
6. Make segments meaningfully different; avoid paraphrased duplicates.
7. For each scoring dimension return an integer from 1 to 10.
8. competitive_headroom means how favorable the competitive situation is: 10 is attractive,
   differentiated or underserved; 1 is saturated and hard to win.
9. Give concise rationale for the most important assumptions behind each segment.

Scoring dimensions:
- pain_intensity: urgency/strength of the underlying problem or desire;
- purchase_intent: likelihood the segment is already looking for a solution;
- willingness_to_pay: expected propensity to pay for this product category;
- ease_of_targeting: how concretely this segment can be found online;
- market_size: plausible relative size of the reachable segment;
- competitive_headroom: ability to win versus existing alternatives;
- speed_of_validation: how quickly a cheap acquisition experiment can produce a signal.

Return the requested structured schema only.
"""


class ICPEngine:
    def __init__(self, provider: LLMProvider | None) -> None:
        self._provider = provider

    async def generate(self, product_profile: dict[str, Any]) -> ICPRankingResult:
        if self._provider is None:
            generation = self._fallback_generation(product_profile)
        else:
            generation = await self._provider.parse(
                messages=self._build_messages(product_profile),
                response_model=ICPGeneration,
            )
        return self.rank(generation.candidates)

    def rank(self, candidates: list[ICPCandidate]) -> ICPRankingResult:
        scored = [
            RankedICPCandidate(
                candidate=candidate,
                total_score=self.calculate_score(candidate.dimensions),
                score_explanation=self.explain_score(candidate.dimensions),
            )
            for candidate in candidates
        ]
        scored.sort(key=lambda item: (-item.total_score, item.candidate.title.lower()))

        unique: list[RankedICPCandidate] = []
        duplicates: list[RankedICPCandidate] = []
        clusters: dict[str, list[str]] = {}

        for item in scored:
            canonical = self._find_near_duplicate(item.candidate, unique)
            if canonical is None:
                unique.append(item)
                continue
            marked = RankedICPCandidate(
                candidate=item.candidate,
                total_score=item.total_score,
                score_explanation=item.score_explanation,
                duplicate_of=canonical.candidate.title,
            )
            duplicates.append(marked)
            clusters.setdefault(canonical.candidate.title, []).append(item.candidate.title)

        if len(unique) < 10:
            unique.extend(duplicates[: 10 - len(unique)])

        unique.sort(key=lambda item: (-item.total_score, item.candidate.title.lower()))
        return ICPRankingResult(ranked=unique, duplicate_clusters=clusters)

    def calculate_score(self, dimensions: ICPDimensionScores) -> float:
        values = dimensions.model_dump()
        weighted = sum(values[name] * weight for name, weight in SCORE_WEIGHTS.items())
        return round(weighted * 10, 1)

    def explain_score(self, dimensions: ICPDimensionScores) -> str:
        values = dimensions.model_dump()
        strongest = sorted(values.items(), key=lambda item: (-item[1], item[0]))[:2]
        weakest = min(values.items(), key=lambda item: (item[1], item[0]))
        strong_text = ", ".join(f"{name}={score}/10" for name, score in strongest)
        return (
            f"Главные драйверы: {strong_text}. "
            f"Главное ограничение: {weakest[0]}={weakest[1]}/10."
        )

    def _build_messages(self, product_profile: dict[str, Any]) -> list[LLMMessage]:
        facts = "\n".join(
            f"{key}: {value}"
            for key, value in product_profile.items()
            if value not in (None, "", [], {}) and key not in {"id", "status"}
        )
        return [
            LLMMessage(role="system", content=SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=f"Founder-confirmed ProductProfile:\n{facts}",
            ),
        ]

    def _find_near_duplicate(
        self,
        candidate: ICPCandidate,
        existing: list[RankedICPCandidate],
    ) -> RankedICPCandidate | None:
        candidate_tokens = self._tokens(f"{candidate.title} {candidate.description}")
        for item in existing:
            other_tokens = self._tokens(
                f"{item.candidate.title} {item.candidate.description}"
            )
            if self._jaccard(candidate_tokens, other_tokens) >= 0.72:
                return item
        return None

    def _tokens(self, text: str) -> set[str]:
        words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())
        stopwords = {
            "and",
            "the",
            "for",
            "with",
            "that",
            "this",
            "who",
            "для",
            "или",
            "это",
            "которые",
            "пользователи",
            "люди",
        }
        return {word for word in words if len(word) > 2 and word not in stopwords}

    def _jaccard(self, left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / len(left | right)

    def _fallback_generation(self, product: dict[str, Any]) -> ICPGeneration:
        problem = product.get("problem_or_desire") or product.get("value_proposition") or "need"
        value = product.get("value_proposition") or product.get("description") or "the product"
        alternatives = product.get("known_competitors") or ["manual solution", "generic tool"]
        templates = [
            ("Urgent problem solvers", 10, 9, 8, 8, 7, 7, 10),
            ("Active alternative switchers", 8, 9, 8, 9, 7, 8, 9),
            ("High-frequency power users", 8, 8, 9, 8, 6, 8, 8),
            ("Outcome-maximizing buyers", 8, 8, 9, 7, 6, 7, 8),
            ("Convenience-first users", 7, 7, 7, 8, 8, 7, 9),
            ("Category enthusiasts", 6, 7, 7, 9, 7, 6, 9),
            ("Curious early adopters", 5, 6, 6, 9, 8, 7, 10),
            ("Repeat habit builders", 7, 7, 8, 7, 6, 8, 7),
            ("Budget-conscious testers", 6, 7, 4, 8, 8, 6, 10),
            ("Community-led discoverers", 6, 6, 6, 10, 7, 7, 9),
            ("Lapsed category users", 6, 6, 6, 7, 7, 8, 7),
            ("Premium experience seekers", 7, 7, 10, 6, 5, 8, 7),
        ]
        candidates: list[ICPCandidate] = []
        for title, pain, intent, wtp, targeting, size, headroom, speed in templates:
            candidates.append(
                ICPCandidate(
                    title=title,
                    description=f"Hypothesis segment around {problem}: {title.lower()}.",
                    pain=str(problem),
                    desired_outcome=str(value),
                    trigger=f"A moment makes {problem} salient enough to seek a solution.",
                    willingness_to_pay_hypothesis=(
                        "Willingness to pay depends on urgency and perceived outcome quality."
                    ),
                    alternatives=list(alternatives),
                    message_hook=f"Get {value} when the need becomes relevant.",
                    dimensions=ICPDimensionScores(
                        pain_intensity=pain,
                        purchase_intent=intent,
                        willingness_to_pay=wtp,
                        ease_of_targeting=targeting,
                        market_size=size,
                        competitive_headroom=headroom,
                        speed_of_validation=speed,
                    ),
                    rationale=["Deterministic local fallback for contract testing."],
                )
            )
        return ICPGeneration(candidates=candidates)
