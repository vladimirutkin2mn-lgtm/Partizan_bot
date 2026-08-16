from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_play_service import distribution_play_service
from app.distribution_schemas import DistributionOpportunityView
from app.icp_service import icp_service
from app.main import app
from app.opportunity_enrichment import (
    RedditPolicyProposalBuilder,
    opportunity_enrichment_service,
)
from app.product_intake import product_intake_service
from app.search import DiscoveryQuery, SearchHit, SearchProvider, SourceClass

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()


def _confirmed_product() -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 200\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _opportunity(product_id: str, platform: str) -> dict:
    distribution = client.get(f"/v1/products/{product_id}/distribution")
    assert distribution.status_code == 200
    return next(
        item
        for item in distribution.json()["opportunities"]
        if item["platform"] == platform
    )


class EnrichmentProvider(SearchProvider):
    async def search(
        self,
        discovery_query: DiscoveryQuery,
        limit: int = 5,
    ) -> list[SearchHit]:
        query = discovery_query.query
        if "site:reddit.com/r/" in query:
            rows = [
                (
                    "Community rules",
                    "https://www.reddit.com/r/relationships/about/rules/",
                    (
                        "No self-promotion. External links are not allowed. "
                        "Disclosure required for affiliated recommendations."
                    ),
                )
            ]
        elif "site:instagram.com" in query:
            rows = [
                (
                    "Recent relationship Reel",
                    "https://www.instagram.com/reel/ABC123/",
                    "Recent relationship advice Reel from an active creator with 25k followers.",
                )
            ]
        elif "site:tiktok.com" in query:
            rows = [
                (
                    "Breakup advice video",
                    "https://www.tiktok.com/@relationshipcoach/video/1001",
                    "Recent #breakuptok video from an active creator with 40k followers.",
                )
            ]
        elif "site:t.me/" in query:
            rows = [
                (
                    "Relationship community",
                    "https://t.me/relationship_daily",
                    "Active daily posts and comments, 12k subscribers.",
                )
            ]
        else:
            rows = []
        return [
            SearchHit(
                title=title,
                url=url,
                snippet=snippet,
                query=query,
                source_class=discovery_query.source_class,
            )
            for title, url, snippet in rows[:limit]
        ]


class FailingEnrichmentProvider(SearchProvider):
    async def search(
        self,
        discovery_query: DiscoveryQuery,
        limit: int = 5,
    ) -> list[SearchHit]:
        raise RuntimeError("temporary enrichment outage")


def test_reddit_policy_proposal_keeps_ambiguous_fields_unknown() -> None:
    reddit = DistributionOpportunityView.model_validate(
        {
            "id": "00000000-0000-0000-0000-000000000002",
            "icp_id": "00000000-0000-0000-0000-000000000003",
            "platform": "REDDIT",
            "kind": "SUBREDDIT",
            "canonical_key": "subreddit:relationships",
            "title": "r/relationships",
            "url": "https://www.reddit.com/r/relationships/",
            "metadata": {},
            "evidence": [],
        }
    )
    hit = SearchHit(
        title="Rules",
        url="https://www.reddit.com/r/relationships/about/rules/",
        snippet=(
            "No self-promotion. External links are not allowed. "
            "Disclosure required for affiliated recommendations."
        ),
        query="rules",
        source_class=SourceClass.COMMUNITY,
    )

    proposal = RedditPolicyProposalBuilder().build(
        reddit,
        [hit],
        generated_at=datetime.now(UTC),
    )

    assert proposal.commercial_participation == "UNKNOWN"
    assert proposal.self_promotion == "DISALLOWED"
    assert proposal.links == "DISALLOWED"
    assert proposal.standalone_posts == "UNKNOWN"
    assert proposal.comments == "UNKNOWN"
    assert proposal.disclosure == "REQUIRED"
    assert proposal.has_unknowns is True


def test_instagram_enrichment_persists_media_action_target(monkeypatch) -> None:
    product_id = _confirmed_product()
    instagram = _opportunity(product_id, "INSTAGRAM")
    monkeypatch.setattr(opportunity_enrichment_service, "_provider", EnrichmentProvider())

    response = client.post(
        f"/v1/products/{product_id}/distribution-opportunities/{instagram['id']}/enrich"
    )

    assert response.status_code == 200
    targets = response.json()["opportunity"]["metadata"]["enrichment"]["action_targets"]
    assert targets[0]["url"] == "https://www.instagram.com/reel/ABC123/"

    stored = _opportunity(product_id, "INSTAGRAM")
    assert stored["metadata"]["enrichment"]["action_targets"] == targets
    assert stored["metadata"]["enrichment"]["size_evidence"] == ["25k followers"]


def test_reddit_enrichment_proposes_policy_without_applying_it(monkeypatch) -> None:
    product_id = _confirmed_product()
    reddit = _opportunity(product_id, "REDDIT")
    monkeypatch.setattr(opportunity_enrichment_service, "_provider", EnrichmentProvider())

    response = client.post(
        f"/v1/products/{product_id}/distribution-opportunities/{reddit['id']}/enrich"
    )

    assert response.status_code == 200
    proposal = response.json()["policy_proposal"]
    assert proposal["self_promotion"] == "DISALLOWED"
    assert proposal["links"] == "DISALLOWED"
    assert proposal["comments"] == "UNKNOWN"

    policy = client.get(
        f"/v1/distribution-opportunities/{reddit['id']}/community-policy"
    )
    assert policy.status_code == 404


def test_failed_enrichment_keeps_existing_discovery_evidence(monkeypatch) -> None:
    product_id = _confirmed_product()
    telegram = _opportunity(product_id, "TELEGRAM")
    original_evidence = list(telegram["evidence"])
    monkeypatch.setattr(
        opportunity_enrichment_service,
        "_provider",
        FailingEnrichmentProvider(),
    )

    response = client.post(
        f"/v1/products/{product_id}/distribution-opportunities/{telegram['id']}/enrich"
    )

    assert response.status_code == 200
    assert response.json()["partial_failure"] is True
    stored = _opportunity(product_id, "TELEGRAM")
    assert stored["evidence"] == original_evidence
