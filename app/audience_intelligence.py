import asyncio
from dataclasses import dataclass

from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform
from app.marketing_intelligence import MarketingTask, skill_router
from app.platform_discovery import (
    PlatformCandidate,
    PlatformDiscoveryAdapter,
    PlatformDiscoveryRequest,
    default_platform_adapters,
)
from app.schemas import ICPView, ProductProfileView
from app.search import SearchHit, SearchProvider

DEMAND_INTENT_MARKERS = (
    "looking for",
    "recommend",
    "recommendation",
    "alternative",
    "alternatives",
    "help",
    "need a",
    "need an",
    "how to",
    "how do",
    "which one",
    "worth it",
    "review",
    "reviews",
    "struggling",
    "frustrated",
    "ищу",
    "посоветуйте",
    "рекоменд",
    "альтернатив",
    "нужен",
    "нужна",
    "нужно",
    "как ",
    "проблем",
    "не могу",
    "стоит ли",
)

COMMERCIAL_INTENT_MARKERS = (
    "price",
    "pricing",
    "cost",
    "buy",
    "subscribe",
    "subscription",
    "trial",
    "demo",
    "pay",
    "budget",
    "цена",
    "стоимость",
    "купить",
    "подпис",
    "оплат",
    "бюджет",
)


@dataclass(frozen=True, slots=True)
class PlatformDiscoveryFailure:
    platform: DistributionPlatform
    query: str
    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class AudienceEvidenceSummary:
    fit_ratio: float
    pain_ratio: float
    trigger_ratio: float
    alternative_ratio: float
    demand_intent_hits: int
    commercial_intent_hits: int
    evidence_count: int
    independent_evidence_count: int
    confidence: str
    matched_terms: tuple[str, ...]
    observed_signal_tags: tuple[str, ...]

    def as_metadata(self) -> dict:
        return {
            "fit_ratio": self.fit_ratio,
            "pain_ratio": self.pain_ratio,
            "trigger_ratio": self.trigger_ratio,
            "alternative_ratio": self.alternative_ratio,
            "demand_intent_hits": self.demand_intent_hits,
            "commercial_intent_hits": self.commercial_intent_hits,
            "evidence_count": self.evidence_count,
            "independent_evidence_count": self.independent_evidence_count,
            "confidence": self.confidence,
            "matched_terms": list(self.matched_terms),
            "observed_signal_tags": list(self.observed_signal_tags),
            "query_terms_count_as_evidence": False,
        }


class AudienceIntelligenceEngine:
    def __init__(
        self,
        provider: SearchProvider,
        max_concurrency: int = 4,
        adapters: list[PlatformDiscoveryAdapter] | None = None,
    ) -> None:
        self._provider = provider
        self._max_concurrency = max_concurrency
        self._adapters = adapters or default_platform_adapters()
        self._last_failures: list[PlatformDiscoveryFailure] = []

    @property
    def last_failures(self) -> list[PlatformDiscoveryFailure]:
        return list(self._last_failures)

    async def discover(
        self,
        product: ProductProfileView,
        icps: list[ICPView],
        per_query_limit: int = 5,
        max_opportunities: int = 80,
    ) -> list[DistributionOpportunitySeed]:
        jobs: list[tuple[ICPView, PlatformDiscoveryAdapter, PlatformDiscoveryRequest]] = []
        for icp in icps:
            for adapter in self._adapters:
                jobs.extend(
                    (icp, adapter, request)
                    for request in adapter.build_requests(product, icp)
                )

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run(
            icp: ICPView,
            adapter: PlatformDiscoveryAdapter,
            request: PlatformDiscoveryRequest,
        ) -> tuple[
            ICPView,
            PlatformDiscoveryAdapter,
            PlatformDiscoveryRequest,
            list[SearchHit],
            Exception | None,
        ]:
            try:
                async with semaphore:
                    hits = await self._provider.search(
                        request.discovery_query,
                        limit=per_query_limit,
                    )
                return icp, adapter, request, hits, None
            except Exception as exc:
                return icp, adapter, request, [], exc

        batches = await asyncio.gather(
            *(run(icp, adapter, request) for icp, adapter, request in jobs)
        )
        self._last_failures = []
        opportunities: dict[
            tuple[str, DistributionPlatform, str], DistributionOpportunitySeed
        ] = {}

        for icp, adapter, request, hits, error in batches:
            if error is not None:
                self._last_failures.append(
                    PlatformDiscoveryFailure(
                        platform=request.platform,
                        query=request.discovery_query.query,
                        error_type=type(error).__name__,
                        message=str(error)[:500],
                    )
                )
                continue
            for candidate in adapter.candidates(request, hits):
                seed = self._candidate_to_seed(icp, candidate)
                self._merge(opportunities, seed)

        ranked = sorted(
            opportunities.values(),
            key=lambda item: (-(item.relevance_score or 0), item.canonical_key),
        )
        return ranked[:max_opportunities]

    def build_queries(
        self,
        product: ProductProfileView,
        icp: ICPView,
    ) -> list[PlatformDiscoveryRequest]:
        return [
            request
            for adapter in self._adapters
            for request in adapter.build_requests(product, icp)
        ]

    def _candidate_to_seed(
        self,
        icp: ICPView,
        candidate: PlatformCandidate,
    ) -> DistributionOpportunitySeed:
        score, rationale, summary = self._score(icp, candidate.hits, candidate.platform)
        metadata = dict(candidate.metadata)
        metadata["research_signals"] = summary.as_metadata()
        metadata["marketing_intelligence_skills"] = [
            pack.name for pack in skill_router.select(MarketingTask.AUDIENCE_DISCOVERY)
        ]
        return DistributionOpportunitySeed(
            icp_id=icp.id,
            platform=candidate.platform,
            kind=candidate.kind,
            canonical_key=candidate.canonical_key,
            title=candidate.title,
            url=candidate.url,
            relevance_score=score,
            rationale=rationale,
            metadata=metadata,
            evidence=[self._evidence(icp, hit) for hit in candidate.hits],
        )

    def _merge(
        self,
        opportunities: dict[
            tuple[str, DistributionPlatform, str],
            DistributionOpportunitySeed,
        ],
        seed: DistributionOpportunitySeed,
    ) -> None:
        key = (str(seed.icp_id), seed.platform, seed.canonical_key)
        existing = opportunities.get(key)
        if existing is None:
            opportunities[key] = seed
            return
        known_urls = {str(item.get("url", "")) for item in existing.evidence}
        for evidence in seed.evidence:
            if str(evidence.get("url", "")) not in known_urls:
                existing.evidence.append(evidence)
        existing.metadata = self._merge_metadata(existing.metadata, seed.metadata)
        if (seed.relevance_score or 0) > (existing.relevance_score or 0):
            existing.relevance_score = seed.relevance_score
            existing.rationale = seed.rationale
            if "research_signals" in seed.metadata:
                existing.metadata["research_signals"] = seed.metadata["research_signals"]

    def _merge_metadata(self, existing: dict, incoming: dict) -> dict:
        merged = dict(existing)
        for key, value in incoming.items():
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value
                continue
            if isinstance(merged[key], list) and isinstance(value, list):
                combined = list(merged[key])
                for item in value:
                    if item not in combined:
                        combined.append(item)
                merged[key] = combined
        return merged

    def _score(
        self,
        icp: ICPView,
        hits: list[SearchHit],
        platform: DistributionPlatform,
    ) -> tuple[float, str, AudienceEvidenceSummary]:
        summary = self._summarize_evidence(icp, hits)
        repeated_evidence_bonus = min(
            12.0,
            max(0, summary.independent_evidence_count - 1) * 3.0,
        )
        score = round(
            min(
                100.0,
                10.0
                + summary.fit_ratio * 25.0
                + summary.pain_ratio * 15.0
                + summary.trigger_ratio * 10.0
                + summary.alternative_ratio * 5.0
                + min(12.0, summary.demand_intent_hits * 4.0)
                + min(8.0, summary.commercial_intent_hits * 4.0)
                + repeated_evidence_bonus,
            ),
            1,
        )
        preview = ", ".join(summary.matched_terms[:5]) or "no observed ICP term overlap"
        rationale = (
            f"{platform.value} observed audience evidence: confidence={summary.confidence}; "
            f"fit={summary.fit_ratio:.2f}; pain={summary.pain_ratio:.2f}; "
            f"trigger={summary.trigger_ratio:.2f}; demand_intent_hits="
            f"{summary.demand_intent_hits}; commercial_intent_hits="
            f"{summary.commercial_intent_hits}; evidence_count={summary.evidence_count}; "
            f"matched={preview}. Search-query terms are provenance only, not evidence."
        )
        return score, rationale, summary

    def _summarize_evidence(
        self,
        icp: ICPView,
        hits: list[SearchHit],
    ) -> AudienceEvidenceSummary:
        fit_tokens = self._tokens(f"{icp.title} {icp.description}")
        pain_tokens = self._tokens(icp.pain)
        trigger_tokens = self._tokens(icp.trigger)
        alternative_tokens = self._tokens(" ".join(icp.alternatives))
        target_tokens = fit_tokens | pain_tokens | trigger_tokens | alternative_tokens

        # Search queries intentionally do not participate here. They describe what Partizan asked
        # the search provider to find, not what a public source actually said.
        observed_text = " ".join(self._hit_text(hit) for hit in hits)
        evidence_tokens = self._tokens(observed_text)
        matched = tuple(sorted(target_tokens & evidence_tokens)[:12])

        demand_hits = sum(
            self._contains_any(self._hit_text(hit), DEMAND_INTENT_MARKERS) for hit in hits
        )
        commercial_hits = sum(
            self._contains_any(self._hit_text(hit), COMMERCIAL_INTENT_MARKERS) for hit in hits
        )
        independent_count = len({hit.url for hit in hits if hit.url})
        fit_ratio = self._overlap_ratio(fit_tokens, evidence_tokens, 12)
        pain_ratio = self._overlap_ratio(pain_tokens, evidence_tokens, 8)
        trigger_ratio = self._overlap_ratio(trigger_tokens, evidence_tokens, 8)
        alternative_ratio = self._overlap_ratio(alternative_tokens, evidence_tokens, 8)
        confidence = self._confidence(
            fit_ratio=fit_ratio,
            pain_ratio=pain_ratio,
            trigger_ratio=trigger_ratio,
            demand_hits=demand_hits,
            commercial_hits=commercial_hits,
            independent_count=independent_count,
        )
        aggregate_tags: set[str] = set()
        for hit in hits:
            aggregate_tags.update(self._signal_tags(icp, hit))

        return AudienceEvidenceSummary(
            fit_ratio=fit_ratio,
            pain_ratio=pain_ratio,
            trigger_ratio=trigger_ratio,
            alternative_ratio=alternative_ratio,
            demand_intent_hits=demand_hits,
            commercial_intent_hits=commercial_hits,
            evidence_count=len(hits),
            independent_evidence_count=independent_count,
            confidence=confidence,
            matched_terms=matched,
            observed_signal_tags=tuple(sorted(aggregate_tags)),
        )

    def _confidence(
        self,
        *,
        fit_ratio: float,
        pain_ratio: float,
        trigger_ratio: float,
        demand_hits: int,
        commercial_hits: int,
        independent_count: int,
    ) -> str:
        problem_signal = max(pain_ratio, trigger_ratio)
        intent_hits = demand_hits + commercial_hits
        if independent_count >= 3 and demand_hits >= 2 and (
            problem_signal >= 0.25 or fit_ratio >= 0.35
        ):
            return "HIGH"
        if independent_count >= 2 and (
            (intent_hits >= 1 and fit_ratio >= 0.15) or problem_signal >= 0.25
        ):
            return "MEDIUM"
        if independent_count == 1 and intent_hits >= 1 and problem_signal >= 0.5:
            return "MEDIUM"
        return "LOW"

    def _evidence(self, icp: ICPView, hit: SearchHit) -> dict:
        return {
            "query": hit.query,
            "title": hit.title,
            "url": hit.url,
            "snippet": hit.snippet,
            "source_class": hit.source_class.value,
            "signal_tags": self._signal_tags(icp, hit),
        }

    def _signal_tags(self, icp: ICPView, hit: SearchHit) -> list[str]:
        text = self._hit_text(hit)
        tokens = self._tokens(text)
        tags: list[str] = []
        if self._tokens(f"{icp.title} {icp.description}") & tokens:
            tags.append("icp_fit")
        if self._tokens(icp.pain) & tokens:
            tags.append("pain")
        if self._tokens(icp.trigger) & tokens:
            tags.append("trigger")
        if self._tokens(" ".join(icp.alternatives)) & tokens:
            tags.append("alternative")
        if self._contains_any(text, DEMAND_INTENT_MARKERS):
            tags.append("demand_intent")
        if self._contains_any(text, COMMERCIAL_INTENT_MARKERS):
            tags.append("commercial_intent")
        return tags

    def _hit_text(self, hit: SearchHit) -> str:
        return f"{hit.title} {hit.snippet}".strip()

    def _contains_any(self, text: str, markers: tuple[str, ...]) -> bool:
        lowered = text.lower()
        return any(marker in lowered for marker in markers)

    def _overlap_ratio(
        self,
        target_tokens: set[str],
        evidence_tokens: set[str],
        denominator_cap: int,
    ) -> float:
        if not target_tokens:
            return 0.0
        denominator = max(1, min(len(target_tokens), denominator_cap))
        return round(min(1.0, len(target_tokens & evidence_tokens) / denominator), 3)

    def _tokens(self, text: str) -> set[str]:
        import re

        words = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())
        stopwords = {
            "and",
            "the",
            "for",
            "with",
            "that",
            "this",
            "from",
            "для",
            "или",
            "это",
            "которые",
            "люди",
        }
        return {word for word in words if len(word) > 2 and word not in stopwords}
