from __future__ import annotations

from app.audience_intelligence import AudienceIntelligenceEngine
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.platform_discovery import (
    InstagramDiscoveryAdapter,
    PlatformDiscoveryAdapter,
    PlatformDiscoveryRequest,
    RedditDiscoveryAdapter,
    TelegramDiscoveryAdapter,
    TikTokDiscoveryAdapter,
)
from app.schemas import ICPView, ProductProfileView
from app.search import DiscoveryQuery, SearchProvider, SourceClass

# The old Telegram discovery used only two queries per ICP. That is too brittle for
# customer acquisition: one or two obvious event channels can fail preflight and leave
# the system with no executable opportunity even when adjacent communities are active.
# Keep a bounded but broader search budget so discovery explores multiple participation
# angles before it gives up.
TELEGRAM_DISCOVERY_QUERY_BUDGET = 6
TARGET_READY_TELEGRAM_OPPORTUNITIES = 5

_RUSSIAN_LANGUAGE_MARKERS = ("ru", "russian", "рус", "русский", "русская")


class ExpandedTelegramDiscoveryAdapter(TelegramDiscoveryAdapter):
    """Broader Telegram discovery without changing publication permissions.

    The adapter still produces research candidates only. Native write access is checked
    later by Telegram publisher preflight. The purpose here is recall: search beyond the
    first obvious event/channel and deliberately include discussion-heavy, educational,
    newcomer and adjacent-topic communities.
    """

    def build_requests(
        self,
        product: ProductProfileView,
        icp: ICPView,
    ) -> list[PlatformDiscoveryRequest]:
        market, language = self._market_language(product)
        requests = list(super().build_requests(product, icp))
        discussion_terms, learning_terms, community_terms = self._participation_terms(language)

        title = self._clean(getattr(icp, "title", ""))
        pain = self._clean(getattr(icp, "pain", ""))
        trigger = self._clean(getattr(icp, "trigger", ""))
        alternatives = self._clean(" ".join(getattr(icp, "alternatives", [])[:3]))
        product_theme = self._clean(
            " ".join(
                part
                for part in (
                    getattr(product, "name", ""),
                    getattr(product, "problem_or_desire", "") or "",
                    " ".join(getattr(product, "known_audience", [])[:2]),
                )
                if part
            )
        )

        expansions = [
            (
                OpportunityKind.CHANNEL,
                self._topic(title, discussion_terms),
                self._query(
                    title,
                    discussion_terms,
                    "Telegram discussion comments",
                    market,
                    language,
                ),
            ),
            (
                OpportunityKind.CHANNEL,
                self._topic(title or pain, learning_terms),
                self._query(
                    title or pain,
                    learning_terms,
                    "Telegram education advice",
                    market,
                    language,
                ),
            ),
            (
                OpportunityKind.GROUP,
                self._topic(title or trigger, community_terms),
                self._query(
                    title or trigger,
                    community_terms,
                    "Telegram community chat",
                    market,
                    language,
                ),
            ),
            (
                OpportunityKind.CHANNEL,
                self._topic(alternatives or product_theme or title, discussion_terms),
                self._query(
                    alternatives or product_theme or title,
                    discussion_terms,
                    "Telegram adjacent community",
                    market,
                    language,
                ),
            ),
        ]

        for kind, topic, query in expansions:
            if not topic or not query:
                continue
            requests.append(
                PlatformDiscoveryRequest(
                    platform=DistributionPlatform.TELEGRAM,
                    kind=kind,
                    topic=topic,
                    discovery_query=DiscoveryQuery(SourceClass.COMMUNITY, query),
                )
            )

        # Deduplicate semantically identical queries and enforce a hard budget. This is
        # the "search until budget exhausted" boundary used by the autonomous refresh.
        deduped: list[PlatformDiscoveryRequest] = []
        seen: set[str] = set()
        for request in requests:
            key = " ".join(request.discovery_query.query.lower().split())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(request)
            if len(deduped) >= TELEGRAM_DISCOVERY_QUERY_BUDGET:
                break
        return deduped

    @staticmethod
    def _clean(value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @classmethod
    def _topic(cls, anchor: str, intent: str) -> str:
        return cls._clean(f"{anchor} {intent}")[:160]

    @classmethod
    def _query(
        cls,
        anchor: str,
        intent: str,
        surface_hint: str,
        market: str,
        language: str,
    ) -> str:
        anchor = cls._clean(anchor)
        if not anchor:
            return ""
        return cls._clean(
            f"site:t.me {anchor} {intent} {surface_hint} {market} {language}"
        )[:500]

    @staticmethod
    def _participation_terms(language: str) -> tuple[str, str, str]:
        lowered = str(language or "").lower()
        russian = any(marker in lowered for marker in _RUSSIAN_LANGUAGE_MARKERS)
        if russian:
            return (
                "обсуждение комментарии вопросы",
                "новички обучение правила границы советы",
                "сообщество встречи знакомства опыт",
            )
        return (
            "discussion comments questions",
            "newcomers education rules boundaries advice",
            "community meetups introductions experience",
        )


def expanded_platform_adapters() -> list[PlatformDiscoveryAdapter]:
    return [
        ExpandedTelegramDiscoveryAdapter(),
        InstagramDiscoveryAdapter(),
        RedditDiscoveryAdapter(),
        TikTokDiscoveryAdapter(),
    ]


class ExpandedAudienceIntelligenceEngine(AudienceIntelligenceEngine):
    """Audience engine with the broader Telegram search budget enabled by default."""

    def __init__(self, provider: SearchProvider, max_concurrency: int = 4) -> None:
        super().__init__(
            provider,
            max_concurrency=max_concurrency,
            adapters=expanded_platform_adapters(),
        )
