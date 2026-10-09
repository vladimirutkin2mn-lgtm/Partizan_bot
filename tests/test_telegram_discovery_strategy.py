from types import SimpleNamespace
from uuid import uuid4

from app.distribution_types import DistributionPlatform, OpportunityKind
from app.telegram_discovery_strategy import (
    MAX_ADAPTIVE_DISCOVERY_ROUNDS,
    TELEGRAM_DISCOVERY_QUERY_BUDGET,
    TARGET_READY_TELEGRAM_OPPORTUNITIES,
    ExpandedTelegramDiscoveryAdapter,
    expanded_platform_adapters,
)


def _icp() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        title="Femdom и BDSM",
        description="Люди, которые интересуются femdom и безопасными практиками",
        pain="сложно найти понятное сообщество и безопасно познакомиться",
        trigger="хочет попробовать практики или прийти на встречу",
        alternatives=["шибари", "kink", "BDSM вечеринки"],
    )


def _product() -> SimpleNamespace:
    return SimpleNamespace(
        name="FemDom",
        problem_or_desire="знакомство с femdom и BDSM",
        known_audience=["новички в BDSM", "люди с интересом к femdom"],
        market="Russia",
        language="Russian",
    )


def test_expanded_telegram_discovery_uses_full_bounded_query_budget() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)

    requests = adapter.build_requests(_product(), _icp())

    assert len(requests) == TELEGRAM_DISCOVERY_QUERY_BUDGET == 6
    assert TARGET_READY_TELEGRAM_OPPORTUNITIES == 5
    assert MAX_ADAPTIVE_DISCOVERY_ROUNDS == 3
    assert all(item.platform == DistributionPlatform.TELEGRAM for item in requests)
    assert {item.kind for item in requests} == {
        OpportunityKind.CHANNEL,
        OpportunityKind.GROUP,
    }
    normalized = [" ".join(item.discovery_query.query.lower().split()) for item in requests]
    assert len(normalized) == len(set(normalized))


def test_expanded_telegram_discovery_searches_participation_and_adjacent_topics() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)

    requests = adapter.build_requests(_product(), _icp())
    queries = "\n".join(item.discovery_query.query.lower() for item in requests)
    topics = "\n".join(str(item.topic or "").lower() for item in requests)

    assert "обсуждение" in queries
    assert "комментарии" in queries
    assert "новички" in queries
    assert "границы" in queries
    assert "сообщество" in queries
    assert "шибари" in queries or "шибари" in topics
    assert "kink" in queries or "kink" in topics


def test_adaptive_hints_replace_repeated_default_queries_inside_same_budget() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        adaptive_hints=[
            "consent education",
            "beginner questions",
            "relationship dynamics",
            "peer advice",
        ],
    )

    requests = adapter.build_requests(_product(), _icp())
    queries = "\n".join(item.discovery_query.query.lower() for item in requests)

    assert len(requests) == TELEGRAM_DISCOVERY_QUERY_BUDGET
    assert "consent education" in queries
    assert "beginner questions" in queries
    assert "relationship dynamics" in queries
    assert "peer advice" in queries
    assert "telegram discussion comments questions" in queries
    assert "telegram public community replies" in queries


def test_expanded_platform_set_replaces_only_telegram_adapter() -> None:
    adapters = expanded_platform_adapters()

    assert len(adapters) == 4
    assert isinstance(adapters[0], ExpandedTelegramDiscoveryAdapter)
    assert [adapter.platform for adapter in adapters] == [
        DistributionPlatform.TELEGRAM,
        DistributionPlatform.INSTAGRAM,
        DistributionPlatform.REDDIT,
        DistributionPlatform.TIKTOK,
    ]


def test_adaptive_round_can_be_telegram_only() -> None:
    adapters = expanded_platform_adapters(
        telegram_hints=["adjacent audience"],
        telegram_only=True,
    )

    assert len(adapters) == 1
    assert isinstance(adapters[0], ExpandedTelegramDiscoveryAdapter)
    assert adapters[0].platform == DistributionPlatform.TELEGRAM


def test_english_products_get_english_participation_lenses() -> None:
    adapter = ExpandedTelegramDiscoveryAdapter(use_default_research_connector=False)
    product = SimpleNamespace(
        name="Example",
        problem_or_desire="find a useful peer community",
        known_audience=["first-time users"],
        market="US",
        language="English",
    )
    icp = SimpleNamespace(
        id=uuid4(),
        title="People learning a new practice",
        description="Beginners looking for guidance",
        pain="hard to know where to start",
        trigger="ready to ask questions",
        alternatives=["peer groups"],
    )

    requests = adapter.build_requests(product, icp)
    queries = "\n".join(item.discovery_query.query.lower() for item in requests)

    assert "discussion comments questions" in queries
    assert "newcomers education rules boundaries advice" in queries
    assert "community meetups introductions experience" in queries
