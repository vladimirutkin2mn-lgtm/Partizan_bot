from types import SimpleNamespace
from uuid import uuid4

from app.discovery_adjacent_expansion import (
    build_adjacent_discovery_hints,
    interleave_adjacent_hints,
)
from app.distribution_schemas import DistributionOpportunitySeed
from app.distribution_types import DistributionPlatform, OpportunityKind
from app.telegram_discovery_strategy import ExpandedTelegramDiscoveryAdapter


def _product():
    return SimpleNamespace(
        id=uuid4(),
        name="CareerGuide",
        problem_or_desire="find better career opportunities",
        value_proposition="practical career coaching for job seekers",
        description="Career support for people changing jobs",
        known_audience=["job seekers", "career changers"],
        market="US",
        language="English",
    )


def _icp():
    return SimpleNamespace(
        id=uuid4(),
        title="Career changers",
        description="People looking for a new job",
        pain="job search is confusing",
        trigger="starting interviews",
        desired_outcome="find a better role",
        alternatives=["career coach"],
    )


def _opportunity(
    *,
    title="Crypto Women Amsterdam",
    about="Female founders discuss web3 investing, local meetups and startup events",
    audience=72.0,
    confidence=70.0,
    matched_participants=0,
    platform=DistributionPlatform.TELEGRAM,
):
    metadata = {
        "about": about,
        "intelligence_scores": {
            "audience_fit": audience,
            "context_fit": 18.0,
            "actionability": 10.0,
            "confidence": confidence,
            "ranking_score": 54.0,
            "exploration_candidate": True,
        },
        "relevance_guard": {"status": "PASS"},
    }
    if matched_participants:
        metadata["participant_evidence"] = {
            "matched_participant_count": matched_participants,
            "identity_persisted": False,
        }
    return DistributionOpportunitySeed(
        icp_id=_icp().id,
        platform=platform,
        kind=(
            OpportunityKind.GROUP
            if platform == DistributionPlatform.TELEGRAM
            else OpportunityKind.CREATOR_ACCOUNT
        ),
        canonical_key="telegram:adjacent",
        title=title,
        url="https://t.me/cryptowomen",
        relevance_score=75,
        rationale="Strong observed audience fit outside the obvious category.",
        metadata=metadata,
        evidence=[
            {
                "title": "Web3 founders networking night",
                "snippet": "Local founders share startup funding and investing experience.",
                "query": "CareerGuide career changers Telegram community",
                "url": "https://example.com/evidence",
            }
        ],
    )


def test_strong_observed_adjacent_context_produces_bounded_hint() -> None:
    product = _product()
    icp = _icp()

    hints = build_adjacent_discovery_hints(
        product=product,
        icps=SimpleNamespace(icps=[icp]),
        opportunities=[_opportunity(matched_participants=3)],
    )

    assert hints
    normalized = " ".join(hints).casefold()
    assert "crypto" in normalized or "web3" in normalized
    assert "career" not in normalized
    assert "telegram" not in normalized
    assert len(hints) <= 4


def test_search_query_terms_are_never_used_as_adjacent_observed_context() -> None:
    product = _product()
    icp = _icp()
    opportunity = _opportunity(
        title="Local Makers",
        about="Small independent workshops and craft events",
    )
    opportunity.evidence[0]["query"] = "secret-query-only-token Telegram career"

    hints = build_adjacent_discovery_hints(
        product=product,
        icps=SimpleNamespace(icps=[icp]),
        opportunities=[opportunity],
    )

    assert "secret" not in " ".join(hints).casefold()
    assert "query" not in " ".join(hints).casefold()
    assert "token" not in " ".join(hints).casefold()


def test_weak_or_low_confidence_surface_produces_no_adjacent_hint() -> None:
    product = _product()
    icps = SimpleNamespace(icps=[_icp()])

    assert build_adjacent_discovery_hints(
        product=product,
        icps=icps,
        opportunities=[_opportunity(audience=40, confidence=80)],
    ) == []
    assert build_adjacent_discovery_hints(
        product=product,
        icps=icps,
        opportunities=[_opportunity(audience=80, confidence=40)],
    ) == []


def test_participant_aggregates_can_strengthen_surface_but_never_supply_hint_vocabulary() -> None:
    product = _product()
    icp = _icp()
    opportunity = _opportunity(matched_participants=5)
    opportunity.metadata["participant_evidence"].update(
        {
            "actor_id": 123456,
            "usernames": ["private_person"],
        }
    )

    hints = build_adjacent_discovery_hints(
        product=product,
        icps=SimpleNamespace(icps=[icp]),
        opportunities=[opportunity],
    )
    joined = " ".join(hints).casefold()

    assert hints
    assert "123456" not in joined
    assert "private_person" not in joined


def test_adjacent_hints_are_telegram_only_and_capped_at_four() -> None:
    product = _product()
    icps = SimpleNamespace(icps=[_icp()])
    opportunities = [
        _opportunity(
            title=f"Context Alpha {index}",
            about=f"Founders market niche{index} meetup ecosystem",
        ).model_copy(update={"canonical_key": f"telegram:{index}"})
        for index in range(8)
    ]
    opportunities.append(
        _opportunity(
            title="Instagram unrelated context",
            about="Should never become Telegram adjacent hypothesis",
            platform=DistributionPlatform.INSTAGRAM,
        )
    )

    hints = build_adjacent_discovery_hints(
        product=product,
        icps=icps,
        opportunities=opportunities,
        max_hints=10,
    )

    assert 1 <= len(hints) <= 4
    assert "instagram" not in " ".join(hints).casefold()


def test_interleave_reserves_one_of_first_four_slots_for_adjacent_exploration() -> None:
    combined = interleave_adjacent_hints(
        ["positive learning", "alternative one", "trigger two", "problem three"],
        ["crypto women moscow", "founder meetup ecosystem"],
    )

    assert combined[:4] == [
        "positive learning",
        "crypto women moscow",
        "alternative one",
        "trigger two",
    ]
    assert "founder meetup ecosystem" in combined[4:]


def test_adjacent_hint_still_keeps_stable_product_and_audience_anchor_in_query() -> None:
    product = _product()
    icp = _icp()
    hint = build_adjacent_discovery_hints(
        product=product,
        icps=SimpleNamespace(icps=[icp]),
        opportunities=[_opportunity()],
        max_hints=1,
    )[0]
    adapter = ExpandedTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        adaptive_hints=[hint],
    )

    queries = "\n".join(
        request.discovery_query.query.casefold()
        for request in adapter.build_requests(product, icp)
    )

    assert "careerguide" in queries
    assert "career changers" in queries
    assert hint.casefold() in queries
