import asyncio
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

from app.schemas import ICPView, ProductProfileView
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass


@dataclass(frozen=True, slots=True)
class ChannelEvidence:
    query: str
    title: str
    url: str
    snippet: str


@dataclass(slots=True)
class ChannelOpportunityCandidate:
    icp_id: UUID
    source_class: SourceClass
    platform: str
    title: str
    url: str
    relevance_score: float
    rationale: str
    evidence: list[ChannelEvidence] = field(default_factory=list)


class SourceAdapter:
    source_class: SourceClass

    def build_queries(self, product: ProductProfileView, icp: ICPView) -> list[DiscoveryQuery]:
        raise NotImplementedError


class CommunityAdapter(SourceAdapter):
    source_class = SourceClass.COMMUNITY

    def build_queries(self, product: ProductProfileView, icp: ICPView) -> list[DiscoveryQuery]:
        market = product.market or "online"
        return [
            DiscoveryQuery(
                source_class=self.source_class,
                query=(
                    f"site:reddit.com {icp.title} {icp.pain} community {market}"
                ),
            ),
            DiscoveryQuery(
                source_class=self.source_class,
                query=f"{icp.trigger} forum discussion community {market}",
            ),
        ]


class CreatorAdapter(SourceAdapter):
    source_class = SourceClass.CREATOR

    def build_queries(self, product: ProductProfileView, icp: ICPView) -> list[DiscoveryQuery]:
        market = product.market or "online"
        return [
            DiscoveryQuery(
                source_class=self.source_class,
                query=f"{icp.title} creator influencer YouTube TikTok Instagram {market}",
            ),
            DiscoveryQuery(
                source_class=self.source_class,
                query=f"{icp.pain} creator expert channel {market}",
            ),
        ]


class NewsletterSiteAdapter(SourceAdapter):
    source_class = SourceClass.NEWSLETTER_SITE

    def build_queries(self, product: ProductProfileView, icp: ICPView) -> list[DiscoveryQuery]:
        market = product.market or "online"
        return [
            DiscoveryQuery(
                source_class=self.source_class,
                query=f"{icp.title} newsletter niche publication blog {market}",
            ),
            DiscoveryQuery(
                source_class=self.source_class,
                query=f"{icp.pain} specialist website newsletter {market}",
            ),
        ]


class ChannelHunter:
    def __init__(self, provider: SearchProvider, max_concurrency: int = 4) -> None:
        self._provider = provider
        self._max_concurrency = max_concurrency
        self._adapters: tuple[SourceAdapter, ...] = (
            CommunityAdapter(),
            CreatorAdapter(),
            NewsletterSiteAdapter(),
        )

    async def discover(
        self,
        product: ProductProfileView,
        icps: list[ICPView],
        per_query_limit: int = 5,
        max_opportunities: int = 60,
    ) -> list[ChannelOpportunityCandidate]:
        jobs: list[tuple[ICPView, DiscoveryQuery]] = []
        for icp in icps:
            for adapter in self._adapters:
                jobs.extend((icp, query) for query in adapter.build_queries(product, icp))

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run(
            icp: ICPView,
            query: DiscoveryQuery,
        ) -> tuple[ICPView, DiscoveryQuery, list[SearchHit]]:
            async with semaphore:
                hits = await self._provider.search(query, limit=per_query_limit)
            return icp, query, hits

        batches = await asyncio.gather(*(run(icp, query) for icp, query in jobs))
        opportunities: dict[tuple[UUID, str], ChannelOpportunityCandidate] = {}

        for icp, query, hits in batches:
            for hit in hits:
                canonical_url = self.canonicalize_url(hit.url)
                if not canonical_url:
                    continue
                relevance_score, rationale = self.score_relevance(icp, hit)
                evidence = ChannelEvidence(
                    query=query.query,
                    title=hit.title,
                    url=hit.url,
                    snippet=hit.snippet,
                )
                key = (icp.id, canonical_url)
                existing = opportunities.get(key)
                if existing is None:
                    opportunities[key] = ChannelOpportunityCandidate(
                        icp_id=icp.id,
                        source_class=hit.source_class,
                        platform=self.platform_from_url(canonical_url),
                        title=hit.title,
                        url=canonical_url,
                        relevance_score=relevance_score,
                        rationale=rationale,
                        evidence=[evidence],
                    )
                    continue
                existing.evidence.append(evidence)
                if relevance_score > existing.relevance_score:
                    existing.relevance_score = relevance_score
                    existing.rationale = rationale
                    existing.title = hit.title

        ranked = sorted(
            opportunities.values(),
            key=lambda item: (-item.relevance_score, item.url),
        )
        return ranked[:max_opportunities]

    def score_relevance(self, icp: ICPView, hit: SearchHit) -> tuple[float, str]:
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
        evidence_tokens = self._tokens(f"{hit.title} {hit.snippet} {hit.query}")
        matched = target_tokens & evidence_tokens
        denominator = max(1, min(len(target_tokens), 12))
        overlap = min(1.0, len(matched) / denominator)
        source_bonus = 15 if self._source_matches_url(hit.source_class, hit.url) else 5
        snippet_bonus = 5 if hit.snippet.strip() else 0
        score = round(min(100.0, 45 + overlap * 35 + source_bonus + snippet_bonus), 1)
        matched_preview = ", ".join(sorted(matched)[:5]) or "query-level match"
        rationale = (
            f"Matched ICP signals ({matched_preview}); found via {hit.source_class.value} "
            f"query; source fit bonus={source_bonus}."
        )
        return score, rationale

    def canonicalize_url(self, url: str) -> str:
        try:
            parts = urlsplit(url.strip())
        except ValueError:
            return ""
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            return ""
        ignored = {
            "utm_source",
            "utm_medium",
            "utm_campaign",
            "utm_term",
            "utm_content",
            "fbclid",
            "gclid",
        }
        query = urlencode(
            [(key, value) for key, value in parse_qsl(parts.query) if key.lower() not in ignored]
        )
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))

    def platform_from_url(self, url: str) -> str:
        host = urlsplit(url).netloc.lower().removeprefix("www.")
        known = {
            "reddit.com": "reddit",
            "youtube.com": "youtube",
            "youtu.be": "youtube",
            "tiktok.com": "tiktok",
            "instagram.com": "instagram",
            "x.com": "x",
            "twitter.com": "x",
            "substack.com": "substack",
        }
        for domain, platform in known.items():
            if host == domain or host.endswith(f".{domain}"):
                return platform
        return host

    def _source_matches_url(self, source_class: SourceClass, url: str) -> bool:
        platform = self.platform_from_url(url)
        if source_class == SourceClass.COMMUNITY:
            return platform in {"reddit", "discord.com", "facebook.com"}
        if source_class == SourceClass.CREATOR:
            return platform in {"youtube", "tiktok", "instagram", "x"}
        return platform not in {"reddit", "youtube", "tiktok", "instagram", "x"}

    def _tokens(self, text: str) -> set[str]:
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
