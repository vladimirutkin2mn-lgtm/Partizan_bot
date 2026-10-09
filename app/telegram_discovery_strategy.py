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
# angles before it gives up. Autonomous refresh may run several bounded rounds when the
# first pass does not produce a useful READY portfolio.
TELEGRAM_DISCOVERY_QUERY_BUDGET = 6
TARGET_READY_TELEGRAM_OPPORTUNITIES = 5
MAX_ADAPTIVE_DISCOVERY_ROUNDS = 3

_RUSSIAN_LANGUAGE_MARKERS = ("ru", "russian", "рус", "русский", "русская")


class ExpandedTelegramDiscoveryAdapter(TelegramDiscoveryAdapter):
    """Broader Telegram discovery without changing publication permissions.

    The adapter still produces research candidates only. Native write access is checked
    later by Telegram publisher preflight. The purpose here is recall: search beyond the
    first obvious event/channel and deliberately include discussion-heavy, educational,
    newcomer and adjacent-topic communities.

    ``adaptive_hints`` are hypotheses learned by the growth loop from earlier discovery
    failures and observed experiment outcomes. They are generic product/audience phrases,
    never customer-specific hard-coded categories.
    """

    def __init__(
        self,
        research_connector=None,
        *,
        use_default_research_connector: bool = True,
        adaptive_hints: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(
            research_connector=research_connector,
            use_default_research_connector=use_default_research_connector,
        )
        self._adaptive_hints = tuple(
            hint
            for hint in (self._clean(item) for item in (adaptive_hints or []))
            if hint
        )

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

        # In adaptive rounds learned hypotheses get first claim on the remaining query
        # budget. This is what makes the next search materially different instead of
        # simply repeating the same six requests after a poor first pass.
        expansions: list[tuple[OpportunityKind, str, str]] = []
        for index, hint in enumerate(self._adaptive_hints[:4]):
            if index % 2 == 0:
                kind = OpportunityKind.CHANNEL
                intent = discussion_terms
                surface_hint = "Telegram discussion comments questions"
            else:
                kind = OpportunityKind.GROUP
                intent = community_terms
                surface_hint = "Telegram public community replies"
            expansions.append(
                (
                    kind,
                    self._topic(hint, intent),
                    self._query(hint, intent, surface_hint, market, language),
                )
            )

        default_expansions = [
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
        expansions.extend(default_expansions)

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

        # Deduplicate semantically identical queries and enforce a hard budget. The
        # autonomous refresh may start another bounded round with different hypotheses,
        # but no individual round is allowed to grow without limit.
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


def expanded_platform_adapters(
    *,
    telegram_hints: list[str] | tuple[str, ...] | None = None,
    telegram_only: bool = False,
) -> list[PlatformDiscoveryAdapter]:
    telegram = ExpandedTelegramDiscoveryAdapter(adaptive_hints=telegram_hints)
    if telegram_only:
        return [telegram]
    return [
        telegram,
        InstagramDiscoveryAdapter(),
        RedditDiscoveryAdapter(),
        TikTokDiscoveryAdapter(),
    ]


class ExpandedAudienceIntelligenceEngine(AudienceIntelligenceEngine):
    """Audience engine with the broader/adaptive Telegram search strategy enabled."""

    def __init__(
        self,
        provider: SearchProvider,
        max_concurrency: int = 4,
        *,
        telegram_hints: list[str] | tuple[str, ...] | None = None,
        telegram_only: bool = False,
    ) -> None:
        super().__init__(
            provider,
            max_concurrency=max_concurrency,
            adapters=expanded_platform_adapters(
                telegram_hints=telegram_hints,
                telegram_only=telegram_only,
            ),
        )
