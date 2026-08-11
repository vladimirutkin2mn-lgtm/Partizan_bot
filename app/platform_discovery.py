import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.distribution_types import DistributionPlatform, OpportunityKind
from app.schemas import ICPView, ProductProfileView
from app.search import DiscoveryQuery, SearchHit, SourceClass


@dataclass(frozen=True, slots=True)
class PlatformDiscoveryRequest:
    platform: DistributionPlatform
    kind: OpportunityKind
    discovery_query: DiscoveryQuery
    topic: str | None = None


@dataclass(slots=True)
class PlatformCandidate:
    platform: DistributionPlatform
    kind: OpportunityKind
    canonical_key: str
    title: str
    url: str | None
    metadata: dict = field(default_factory=dict)
    hits: list[SearchHit] = field(default_factory=list)


class PlatformDiscoveryAdapter(ABC):
    platform: DistributionPlatform

    @abstractmethod
    def build_requests(
        self,
        product: ProductProfileView,
        icp: ICPView,
    ) -> list[PlatformDiscoveryRequest]:
        raise NotImplementedError

    @abstractmethod
    def candidates(
        self,
        request: PlatformDiscoveryRequest,
        hits: list[SearchHit],
    ) -> list[PlatformCandidate]:
        raise NotImplementedError

    def _market_language(self, product: ProductProfileView) -> tuple[str, str]:
        return product.market or "online", product.language or ""


class TelegramDiscoveryAdapter(PlatformDiscoveryAdapter):
    platform = DistributionPlatform.TELEGRAM

    def build_requests(
        self,
        product: ProductProfileView,
        icp: ICPView,
    ) -> list[PlatformDiscoveryRequest]:
        market, language = self._market_language(product)
        return [
            PlatformDiscoveryRequest(
                platform=self.platform,
                kind=OpportunityKind.CHANNEL,
                discovery_query=DiscoveryQuery(
                    SourceClass.COMMUNITY,
                    f"site:t.me {icp.title} {icp.pain} Telegram channel {market} {language}",
                ),
            ),
            PlatformDiscoveryRequest(
                platform=self.platform,
                kind=OpportunityKind.GROUP,
                discovery_query=DiscoveryQuery(
                    SourceClass.COMMUNITY,
                    f"site:t.me {icp.title} {icp.trigger} Telegram group chat {market} {language}",
                ),
            ),
        ]

    def candidates(
        self,
        request: PlatformDiscoveryRequest,
        hits: list[SearchHit],
    ) -> list[PlatformCandidate]:
        candidates: list[PlatformCandidate] = []
        for hit in hits:
            normalized = self._normalize(hit.url, request.kind)
            if normalized is None:
                continue
            canonical_key, canonical_url, handle = normalized
            candidates.append(
                PlatformCandidate(
                    platform=self.platform,
                    kind=request.kind,
                    canonical_key=canonical_key,
                    title=handle,
                    url=canonical_url,
                    metadata={
                        "handle": handle,
                        "surface_kind": request.kind.value,
                        "discovery_query": request.discovery_query.query,
                    },
                    hits=[hit],
                )
            )
        return candidates

    def _normalize(
        self,
        url: str,
        kind: OpportunityKind,
    ) -> tuple[str, str, str] | None:
        parts = urlsplit(url.strip())
        host = parts.netloc.lower().removeprefix("www.")
        segments = [segment for segment in parts.path.split("/") if segment]
        if host not in {"t.me", "telegram.me"} or not segments:
            return None
        if segments[0] == "s" and len(segments) >= 2:
            segments = segments[1:]
        slug = segments[0]
        if slug.startswith("+") or slug.lower() == "joinchat":
            return None
        canonical = f"https://t.me/{slug}"
        return f"{kind.value.lower()}:{slug.lower()}", canonical, slug


class InstagramDiscoveryAdapter(PlatformDiscoveryAdapter):
    platform = DistributionPlatform.INSTAGRAM

    def build_requests(
        self,
        product: ProductProfileView,
        icp: ICPView,
    ) -> list[PlatformDiscoveryRequest]:
        market, language = self._market_language(product)
        return [
            PlatformDiscoveryRequest(
                platform=self.platform,
                kind=OpportunityKind.CREATOR_ACCOUNT,
                discovery_query=DiscoveryQuery(
                    SourceClass.CREATOR,
                    (
                        f"site:instagram.com {icp.title} {icp.pain} "
                        f"creator account {market} {language}"
                    ),
                ),
            )
        ]

    def candidates(
        self,
        request: PlatformDiscoveryRequest,
        hits: list[SearchHit],
    ) -> list[PlatformCandidate]:
        candidates: list[PlatformCandidate] = []
        for hit in hits:
            normalized = self._normalize(hit.url)
            if normalized is None:
                continue
            canonical_key, canonical_url, handle = normalized
            candidates.append(
                PlatformCandidate(
                    platform=self.platform,
                    kind=request.kind,
                    canonical_key=canonical_key,
                    title=f"@{handle}",
                    url=canonical_url,
                    metadata={
                        "account_handle": handle,
                        "creator_theme_evidence": hit.snippet[:500],
                        "discovery_query": request.discovery_query.query,
                    },
                    hits=[hit],
                )
            )
        return candidates

    def _normalize(self, url: str) -> tuple[str, str, str] | None:
        parts = urlsplit(url.strip())
        host = parts.netloc.lower().removeprefix("www.")
        segments = [segment for segment in parts.path.split("/") if segment]
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
        return f"creator:{username.lower()}", canonical, username


class RedditDiscoveryAdapter(PlatformDiscoveryAdapter):
    platform = DistributionPlatform.REDDIT

    def build_requests(
        self,
        product: ProductProfileView,
        icp: ICPView,
    ) -> list[PlatformDiscoveryRequest]:
        market, language = self._market_language(product)
        return [
            PlatformDiscoveryRequest(
                platform=self.platform,
                kind=OpportunityKind.SUBREDDIT,
                discovery_query=DiscoveryQuery(
                    SourceClass.COMMUNITY,
                    f"site:reddit.com/r/ {icp.title} {icp.pain} subreddit {market} {language}",
                ),
            )
        ]

    def candidates(
        self,
        request: PlatformDiscoveryRequest,
        hits: list[SearchHit],
    ) -> list[PlatformCandidate]:
        candidates: list[PlatformCandidate] = []
        for hit in hits:
            normalized = self._normalize(hit.url)
            if normalized is None:
                continue
            canonical_key, canonical_url, subreddit = normalized
            candidates.append(
                PlatformCandidate(
                    platform=self.platform,
                    kind=request.kind,
                    canonical_key=canonical_key,
                    title=f"r/{subreddit}",
                    url=canonical_url,
                    metadata={
                        "subreddit": subreddit,
                        "discovery_query": request.discovery_query.query,
                        "policy_evidence": self._policy_evidence(hit),
                    },
                    hits=[hit],
                )
            )
        return candidates

    def _normalize(self, url: str) -> tuple[str, str, str] | None:
        parts = urlsplit(url.strip())
        host = parts.netloc.lower().removeprefix("www.")
        segments = [segment for segment in parts.path.split("/") if segment]
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
        return f"subreddit:{subreddit.lower()}", canonical, subreddit

    def _policy_evidence(self, hit: SearchHit) -> list[dict]:
        text = f"{hit.title} {hit.snippet}".lower()
        terms = [
            term
            for term in ("rules", "promotion", "self-promotion", "advertising", "links")
            if term in text
        ]
        if not terms:
            return []
        return [
            {
                "source_url": hit.url,
                "matched_terms": terms,
                "snippet": hit.snippet[:500],
            }
        ]


class TikTokDiscoveryAdapter(PlatformDiscoveryAdapter):
    platform = DistributionPlatform.TIKTOK

    def build_requests(
        self,
        product: ProductProfileView,
        icp: ICPView,
    ) -> list[PlatformDiscoveryRequest]:
        market, language = self._market_language(product)
        requests: list[PlatformDiscoveryRequest] = []
        seen_topics: set[str] = set()
        for raw_topic in (icp.title, icp.pain, icp.trigger):
            topic = " ".join(raw_topic.split()).strip()
            if not topic or topic.lower() in seen_topics:
                continue
            seen_topics.add(topic.lower())
            requests.append(
                PlatformDiscoveryRequest(
                    platform=self.platform,
                    kind=OpportunityKind.CONTENT_CLUSTER,
                    topic=topic,
                    discovery_query=DiscoveryQuery(
                        SourceClass.CREATOR,
                        f"site:tiktok.com {topic} videos creators hashtags {market} {language}",
                    ),
                )
            )
        return requests

    def candidates(
        self,
        request: PlatformDiscoveryRequest,
        hits: list[SearchHit],
    ) -> list[PlatformCandidate]:
        relevant_hits = [hit for hit in hits if self._is_tiktok_url(hit.url)]
        if not request.topic or not relevant_hits:
            return []
        topic = request.topic
        return [
            PlatformCandidate(
                platform=self.platform,
                kind=request.kind,
                canonical_key=f"topic:{self._slug(topic)}",
                title=topic,
                url=None,
                metadata={
                    "topic": topic,
                    "discovery_query": request.discovery_query.query,
                    "evidence_count": len(relevant_hits),
                    "creator_handles": self._creator_handles(relevant_hits),
                    "hashtags": self._hashtags(relevant_hits),
                },
                hits=relevant_hits,
            )
        ]

    def _is_tiktok_url(self, url: str) -> bool:
        try:
            host = urlsplit(url.strip()).netloc.lower().removeprefix("www.")
        except ValueError:
            return False
        return host == "tiktok.com" or host.endswith(".tiktok.com")

    def _creator_handles(self, hits: list[SearchHit]) -> list[str]:
        handles: set[str] = set()
        for hit in hits:
            segments = [segment for segment in urlsplit(hit.url).path.split("/") if segment]
            if segments and segments[0].startswith("@"):
                handles.add(segments[0])
        return sorted(handles)[:20]

    def _hashtags(self, hits: list[SearchHit]) -> list[str]:
        hashtags: set[str] = set()
        for hit in hits:
            hashtags.update(re.findall(r"#[\w]+", f"{hit.title} {hit.snippet}"))
        return sorted(hashtags)[:30]

    def _slug(self, text: str) -> str:
        tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", text.lower())
        return "-".join(tokens[:12])[:180] or "topic"


def default_platform_adapters() -> list[PlatformDiscoveryAdapter]:
    return [
        TelegramDiscoveryAdapter(),
        InstagramDiscoveryAdapter(),
        RedditDiscoveryAdapter(),
        TikTokDiscoveryAdapter(),
    ]
