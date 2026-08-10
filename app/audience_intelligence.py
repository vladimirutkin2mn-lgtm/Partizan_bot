import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.schemas import ICPView, ProductProfileView
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass


@dataclass(frozen=True, slots=True)
class PlatformDiscoveryQuery:
    platform: DistributionPlatform
    kind: OpportunityKind
    discovery_query: DiscoveryQuery
    topic: str | None = None


class AudienceIntelligenceEngine:
    def __init__(self, provider: SearchProvider, max_concurrency: int = 4) -> None:
        self._provider = provider
        self._max_concurrency = max_concurrency

    async def discover(
        self,
        product: ProductProfileView,
        icps: list[ICPView],
        per_query_limit: int = 5,
        max_opportunities: int = 80,
    ) -> list[DistributionOpportunitySeed]:
        jobs: list[tuple[ICPView, PlatformDiscoveryQuery]] = []
        for icp in icps:
            jobs.extend((icp, query) for query in self.build_queries(product, icp))

        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def run(
            icp: ICPView,
            platform_query: PlatformDiscoveryQuery,
        ) -> tuple[ICPView, PlatformDiscoveryQuery, list[SearchHit]]:
            async with semaphore:
                hits = await self._provider.search(
                    platform_query.discovery_query,
                    limit=per_query_limit,
                )
            return icp, platform_query, hits

        batches = await asyncio.gather(*(run(icp, query) for icp, query in jobs))
        opportunities: dict[
            tuple[str, DistributionPlatform, str], DistributionOpportunitySeed
        ] = {}

        for icp, platform_query, hits in batches:
            relevant_hits = [
                hit for hit in hits if self._matches_platform(platform_query.platform, hit.url)
            ]
            if platform_query.platform == DistributionPlatform.TIKTOK:
                seed = self._build_tiktok_cluster(icp, platform_query, relevant_hits)
                if seed is not None:
                    self._merge(opportunities, seed)
                continue

            for hit in relevant_hits:
                normalized = self._normalize_platform_url(
                    platform_query.platform,
                    platform_query.kind,
                    hit.url,
                )
                if normalized is None:
                    continue
                canonical_key, canonical_url, title = normalized
                score, rationale = self._score(icp, [hit], platform_query.platform)
                seed = DistributionOpportunitySeed(
                    icp_id=icp.id,
                    platform=platform_query.platform,
                    kind=platform_query.kind,
                    canonical_key=canonical_key,
                    title=title or hit.title,
                    url=canonical_url,
                    relevance_score=score,
                    rationale=rationale,
                    metadata={"discovery_query": platform_query.discovery_query.query},
                    evidence=[self._evidence(hit)],
                )
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
    ) -> list[PlatformDiscoveryQuery]:
        market = product.market or "online"
        language = product.language or ""
        queries = [
            PlatformDiscoveryQuery(
                platform=DistributionPlatform.TELEGRAM,
                kind=OpportunityKind.CHANNEL,
                discovery_query=DiscoveryQuery(
                    SourceClass.COMMUNITY,
                    f"site:t.me {icp.title} {icp.pain} Telegram channel {market} {language}",
                ),
            ),
            PlatformDiscoveryQuery(
                platform=DistributionPlatform.TELEGRAM,
                kind=OpportunityKind.GROUP,
                discovery_query=DiscoveryQuery(
                    SourceClass.COMMUNITY,
                    f"site:t.me {icp.title} {icp.trigger} Telegram group chat {market} {language}",
                ),
            ),
            PlatformDiscoveryQuery(
                platform=DistributionPlatform.INSTAGRAM,
                kind=OpportunityKind.CREATOR_ACCOUNT,
                discovery_query=DiscoveryQuery(
                    SourceClass.CREATOR,
                    (
                        f"site:instagram.com {icp.title} {icp.pain} "
                        f"creator account {market} {language}"
                    ),
                ),
            ),
            PlatformDiscoveryQuery(
                platform=DistributionPlatform.REDDIT,
                kind=OpportunityKind.SUBREDDIT,
                discovery_query=DiscoveryQuery(
                    SourceClass.COMMUNITY,
                    f"site:reddit.com/r/ {icp.title} {icp.pain} subreddit {market} {language}",
                ),
            ),
        ]
        seen_topics: set[str] = set()
        for topic in (icp.title, icp.pain, icp.trigger):
            normalized_topic = " ".join(topic.split()).strip()
            if not normalized_topic or normalized_topic.lower() in seen_topics:
                continue
            seen_topics.add(normalized_topic.lower())
            queries.append(
                PlatformDiscoveryQuery(
                    platform=DistributionPlatform.TIKTOK,
                    kind=OpportunityKind.CONTENT_CLUSTER,
                    topic=normalized_topic,
                    discovery_query=DiscoveryQuery(
                        SourceClass.CREATOR,
                        (
                            f"site:tiktok.com {normalized_topic} videos creators hashtags "
                            f"{market} {language}"
                        ),
                    ),
                )
            )
        return queries

    def _build_tiktok_cluster(
        self,
        icp: ICPView,
        platform_query: PlatformDiscoveryQuery,
        hits: list[SearchHit],
    ) -> DistributionOpportunitySeed | None:
        if not hits or not platform_query.topic:
            return None
        topic = platform_query.topic
        canonical_key = f"topic:{self._slug(topic)}"
        score, rationale = self._score(icp, hits, DistributionPlatform.TIKTOK)
        return DistributionOpportunitySeed(
            icp_id=icp.id,
            platform=DistributionPlatform.TIKTOK,
            kind=OpportunityKind.CONTENT_CLUSTER,
            canonical_key=canonical_key,
            title=topic,
            url=None,
            relevance_score=score,
            rationale=rationale,
            metadata={
                "topic": topic,
                "discovery_query": platform_query.discovery_query.query,
                "evidence_count": len(hits),
            },
            evidence=[self._evidence(hit) for hit in hits],
        )

    def _merge(
        self,
        opportunities: dict[tuple[str, DistributionPlatform, str], DistributionOpportunitySeed],
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
        if (seed.relevance_score or 0) > (existing.relevance_score or 0):
            existing.relevance_score = seed.relevance_score
            existing.rationale = seed.rationale

    def _normalize_platform_url(
        self,
        platform: DistributionPlatform,
        kind: OpportunityKind,
        url: str,
    ) -> tuple[str, str, str] | None:
        parts = urlsplit(url.strip())
        host = parts.netloc.lower().removeprefix("www.")
        segments = [segment for segment in parts.path.split("/") if segment]

        if platform == DistributionPlatform.TELEGRAM:
            if host not in {"t.me", "telegram.me"} or not segments:
                return None
            if segments[0] == "s" and len(segments) >= 2:
                segments = segments[1:]
            slug = segments[0]
            if slug.startswith("+") or slug.lower() == "joinchat":
                return None
            canonical = f"https://t.me/{slug}"
            return f"{kind.value.lower()}:{slug.lower()}", canonical, slug

        if platform == DistributionPlatform.INSTAGRAM:
            if not (host == "instagram.com" or host.endswith(".instagram.com")) or not segments:
                return None
            username = segments[0].lstrip("@")
            reserved = {
                "p",
                "reel",
                "reels",
                "stories",
                "explore",
                "accounts",
                "about",
                "direct",
            }
            if username.lower() in reserved:
                return None
            canonical = f"https://www.instagram.com/{username}/"
            return f"creator:{username.lower()}", canonical, f"@{username}"

        if platform == DistributionPlatform.REDDIT:
            if not (host == "reddit.com" or host.endswith(".reddit.com")):
                return None
            lowered = [segment.lower() for segment in segments]
            if "r" not in lowered:
                return None
            index = lowered.index("r")
            if index + 1 >= len(segments):
                return None
            subreddit = segments[index + 1]
            canonical = f"https://www.reddit.com/r/{subreddit}/"
            return f"subreddit:{subreddit.lower()}", canonical, f"r/{subreddit}"

        return None

    def _matches_platform(self, platform: DistributionPlatform, url: str) -> bool:
        try:
            host = urlsplit(url.strip()).netloc.lower().removeprefix("www.")
        except ValueError:
            return False
        if platform == DistributionPlatform.TELEGRAM:
            return host in {"t.me", "telegram.me"}
        if platform == DistributionPlatform.INSTAGRAM:
            return host == "instagram.com" or host.endswith(".instagram.com")
        if platform == DistributionPlatform.REDDIT:
            return host == "reddit.com" or host.endswith(".reddit.com")
        if platform == DistributionPlatform.TIKTOK:
            return host == "tiktok.com" or host.endswith(".tiktok.com")
        return False

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

    def _slug(self, text: str) -> str:
        tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())
        return "-".join(tokens[:12])[:180] or "topic"

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
