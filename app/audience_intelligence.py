import asyncio
from dataclasses import dataclass

from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform
from app.platform_discovery import (
    PlatformCandidate,
    PlatformDiscoveryAdapter,
    PlatformDiscoveryRequest,
    default_platform_adapters,
)
from app.schemas import ICPView, ProductProfileView
from app.search import SearchHit, SearchProvider


@dataclass(frozen=True, slots=True)
class PlatformDiscoveryFailure:
    platform: DistributionPlatform
    query: str
    error_type: str
    message: str


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
        score, rationale = self._score(icp, candidate.hits, candidate.platform)
        return DistributionOpportunitySeed(
            icp_id=icp.id,
            platform=candidate.platform,
            kind=candidate.kind,
            canonical_key=candidate.canonical_key,
            title=candidate.title,
            url=candidate.url,
            relevance_score=score,
            rationale=rationale,
            metadata=dict(candidate.metadata),
            evidence=[self._evidence(hit) for hit in candidate.hits],
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
    ) -> tuple[float, str]:
        target_tokens = self._tokens(
            " ".join(
                [
                    icp.title,
                    icp.description,
                    icp.pain,
                    icp.trigger,
                    " ".join(icp.alternatives),
                ]
            )
        )
        evidence_tokens = self._tokens(
            " ".join(f"{hit.title} {hit.snippet} {hit.query}" for hit in hits)
        )
        matched = target_tokens & evidence_tokens
        denominator = max(1, min(len(target_tokens), 12))
        overlap = min(1.0, len(matched) / denominator)
        evidence_bonus = min(10.0, len(hits) * 2.0)
        score = round(min(100.0, 50 + overlap * 40 + evidence_bonus), 1)
        preview = ", ".join(sorted(matched)[:5]) or "query-level match"
        return (
            score,
            f"{platform.value} audience evidence matched ICP signals ({preview}); "
            f"evidence_count={len(hits)}.",
        )

    def _evidence(self, hit: SearchHit) -> dict:
        return {
            "query": hit.query,
            "title": hit.title,
            "url": hit.url,
            "snippet": hit.snippet,
        }

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
