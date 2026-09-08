import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.distribution_types import DistributionPlatform, OpportunityKind
from app.schemas import ICPView, ProductProfileView
from app.search import DiscoveryQuery, SearchHit, SourceClass
from app.telegram_research import (
    TelegramCommunitySnapshot,
    TelegramResearchConnector,
    TelegramSurfaceKind,
    get_telegram_research_connector,
)


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

    async def enrich_candidates(
        self,
        request: PlatformDiscoveryRequest,
        candidates: list[PlatformCandidate],
    ) -> list[PlatformCandidate]:
        del request
        return candidates

    def _market_language(self, product: ProductProfileView) -> tuple[str, str]:
        return product.market or "online", product.language or ""


class TelegramDiscoveryAdapter(PlatformDiscoveryAdapter):
    platform = DistributionPlatform.TELEGRAM

    def __init__(
        self,
        research_connector: TelegramResearchConnector | None = None,
        *,
        use_default_research_connector: bool = True,
    ) -> None:
        self._research_connector = research_connector
        if research_connector is None and use_default_research_connector:
            self._research_connector = get_telegram_research_connector()

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
                topic=f"{icp.title} {icp.pain}",
                discovery_query=DiscoveryQuery(
                    SourceClass.COMMUNITY,
                    f"site:t.me {icp.title} {icp.pain} Telegram channel {market} {language}",
                ),
            ),
            PlatformDiscoveryRequest(
                platform=self.platform,
                kind=OpportunityKind.GROUP,
                topic=f"{icp.title} {icp.trigger}",
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
                        "native_research_status": "NOT_CHECKED",
                    },
                    hits=[hit],
                )
            )
        return candidates

    async def enrich_candidates(
        self,
        request: PlatformDiscoveryRequest,
        candidates: list[PlatformCandidate],
    ) -> list[PlatformCandidate]:
        connector = self._research_connector
        if connector is None:
            return candidates
        handles = [
            str(candidate.metadata.get("handle") or "")
            for candidate in candidates
            if candidate.metadata.get("handle")
        ]
        snapshots = await connector.discover(
            query=request.topic or request.discovery_query.query,
            known_handles=handles,
        )
        if not snapshots:
            return candidates
        return self._merge_native_snapshots(request, candidates, snapshots)

    def _merge_native_snapshots(
        self,
        request: PlatformDiscoveryRequest,
        candidates: list[PlatformCandidate],
        snapshots: list[TelegramCommunitySnapshot],
    ) -> list[PlatformCandidate]:
        by_handle = {
            str(candidate.metadata.get("handle") or "").lower(): candidate
            for candidate in candidates
            if candidate.metadata.get("handle")
        }
        consumed_handles: set[str] = set()
        enriched: list[PlatformCandidate] = []
        for snapshot in snapshots:
            handle_key = snapshot.username.lower()
            matched = by_handle.get(handle_key)
            if matched is not None:
                consumed_handles.add(handle_key)
            existing_hits = list(matched.hits) if matched is not None else []
            existing_hits.append(self._native_hit(request, snapshot))
            kind = (
                OpportunityKind.GROUP
                if snapshot.kind == TelegramSurfaceKind.GROUP
                else OpportunityKind.CHANNEL
            )
            enriched.append(
                PlatformCandidate(
                    platform=self.platform,
                    kind=kind,
                    canonical_key=f"telegram:{snapshot.entity_id}",
                    title=snapshot.title,
                    url=snapshot.url,
                    metadata=self._native_metadata(request, snapshot),
                    hits=existing_hits,
                )
            )

        enriched.extend(
            candidate
            for candidate in candidates
            if str(candidate.metadata.get("handle") or "").lower() not in consumed_handles
        )
        return enriched

    def _native_hit(
        self,
        request: PlatformDiscoveryRequest,
        snapshot: TelegramCommunitySnapshot,
    ) -> SearchHit:
        context_text = " ".join(
            item.text for item in snapshot.recent_context if item.text
        )[:2400]
        observed = " ".join(
            part for part in (snapshot.title, snapshot.about, context_text) if part
        )
        return SearchHit(
            title=snapshot.title,
            url=snapshot.url,
            snippet=observed[:800],
            query=request.discovery_query.query,
            source_class=SourceClass.COMMUNITY,
            metadata={
                "evidence_type": "telegram_native",
                "telegram_entity_id": snapshot.entity_id,
                "source_checked_at": snapshot.source_checked_at.isoformat(),
                "last_activity_at": (
                    snapshot.last_activity_at.isoformat()
                    if snapshot.last_activity_at is not None
                    else None
                ),
            },
        )

    def _native_metadata(
        self,
        request: PlatformDiscoveryRequest,
        snapshot: TelegramCommunitySnapshot,
    ) -> dict:
        return {
            "handle": snapshot.username,
            "telegram_entity_id": snapshot.entity_id,
            "surface_kind": snapshot.kind.value,
            "discovery_query": request.discovery_query.query,
            "native_research_status": "VERIFIED",
            "source_checked_at": snapshot.source_checked_at.isoformat(),
            "last_activity_at": (
                snapshot.last_activity_at.isoformat()
                if snapshot.last_activity_at is not None
                else None
            ),
            "member_count": snapshot.member_count,
            "about": snapshot.about,
            "surface_capabilities": {
                "comment": snapshot.comment_surface.value,
                "reply": snapshot.reply_surface.value,
                "standalone_post": snapshot.standalone_post_surface.value,
                "publisher_permission_verified": False,
            },
            "linked_discussion_id": snapshot.linked_discussion_id,
            "action_target_url": snapshot.action_target_url,
            "action_target_specific": snapshot.action_target_url is not None,
            "recent_context": [
                {
                    "message_id": item.message_id,
                    "text": item.text,
                    "published_at": (
                        item.published_at.isoformat()
                        if item.published_at is not None
                        else None
                    ),
                    "url": item.url,
                    "matched_terms": list(item.matched_terms),
                }
                for item in snapshot.recent_context
            ],
        }

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
