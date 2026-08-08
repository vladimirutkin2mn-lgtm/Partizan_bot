from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.channel_hunter import ChannelHunter
from app.channel_service import channel_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service
from app.search import (
    DiscoveryQuery,
    MockSearchProvider,
    OpenAIWebSearchProvider,
    SourceClass,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    channel_service.reset()


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
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    confirmed = client.post(f"/v1/products/{product_id}/confirm")
    assert confirmed.status_code == 200
    return product_id


def test_channel_discovery_requires_icps() -> None:
    product_id = _confirmed_product()
    response = client.post(f"/v1/products/{product_id}/channels/discover")
    assert response.status_code == 409


def test_channel_discovery_returns_concrete_ranked_opportunities() -> None:
    product_id = _confirmed_product()
    generated = client.post(f"/v1/products/{product_id}/icps/generate")
    assert generated.status_code == 200

    response = client.post(f"/v1/products/{product_id}/channels/discover")
    assert response.status_code == 200
    body = response.json()
    assert body["top_icp_count"] == 3
    assert body["opportunity_count"] >= 30
    assert len(body["opportunities"]) >= 30
    assert {item["source_type"] for item in body["opportunities"]} == {
        "community",
        "creator",
        "newsletter_site",
    }
    scores = [item["relevance_score"] for item in body["opportunities"]]
    assert scores == sorted(scores, reverse=True)
    assert all(item["url"].startswith("https://") for item in body["opportunities"])
    assert all(item["rationale"] for item in body["opportunities"])
    assert all(item["evidence"] for item in body["opportunities"])


def test_channel_discovery_can_be_retrieved() -> None:
    product_id = _confirmed_product()
    client.post(f"/v1/products/{product_id}/icps/generate")
    generated = client.post(f"/v1/products/{product_id}/channels/discover").json()
    stored = client.get(f"/v1/products/{product_id}/channels")
    assert stored.status_code == 200
    assert stored.json() == generated


def test_url_canonicalization_removes_tracking_and_fragment() -> None:
    hunter = ChannelHunter(MockSearchProvider())
    canonical = hunter.canonicalize_url(
        "HTTPS://Example.com/path/?utm_source=test&keep=1#section"
    )
    assert canonical == "https://example.com/path?keep=1"


def test_openai_search_extracts_only_cited_urls() -> None:
    provider = OpenAIWebSearchProvider(api_key="test-key", model="gpt-5.6-terra")
    response = SimpleNamespace(
        output_text="Two useful sources.",
        output=[
            SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text="Two useful sources.",
                        annotations=[
                            SimpleNamespace(
                                url="https://example.com/community",
                                title="Example Community",
                            ),
                            SimpleNamespace(
                                url="https://example.com/community",
                                title="Duplicate Citation",
                            ),
                        ],
                    )
                ]
            )
        ],
    )
    query = DiscoveryQuery(SourceClass.COMMUNITY, "relationship advice community")
    hits = provider._extract_hits(response, query)
    assert len(hits) == 1
    assert hits[0].url == "https://example.com/community"
    assert hits[0].query == query.query
