from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_play_service import distribution_play_service
from app.icp_service import icp_service
from app.main import app
from app.opportunity_enrichment import opportunity_enrichment_service
from app.outreach_targets import outreach_target_service
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
    outreach_target_service.reset()


class InstagramEnrichmentProvider(SearchProvider):
    async def search(
        self,
        discovery_query: DiscoveryQuery,
        limit: int = 5,
    ) -> list[SearchHit]:
        if "site:instagram.com" not in discovery_query.query:
            return []
        return [
            SearchHit(
                title="Relationship creator Reel",
                url="https://www.instagram.com/reel/ABC123/",
                snippet="Recent relationship advice Reel from an active creator with 25k followers.",
                query=discovery_query.query,
                source_class=SourceClass.CREATOR,
            )
        ]


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


def _instagram_opportunity(product_id: str) -> dict:
    response = client.get(f"/v1/products/{product_id}/distribution")
    assert response.status_code == 200
    return next(
        item for item in response.json()["opportunities"] if item["platform"] == "INSTAGRAM"
    )


async def _enriched_instagram(monkeypatch, product_id: str) -> dict:
    opportunity = _instagram_opportunity(product_id)
    monkeypatch.setattr(
        opportunity_enrichment_service,
        "_provider",
        InstagramEnrichmentProvider(),
    )
    response = client.post(
        f"/v1/products/{product_id}/distribution-opportunities/{opportunity['id']}/enrich"
    )
    assert response.status_code == 200
    return response.json()["opportunity"]


def _public_payload(opportunity: dict) -> dict:
    action_target = opportunity["metadata"]["enrichment"]["action_targets"][0]
    return {
        "opportunity_id": opportunity["id"],
        "target_type": "CREATOR",
        "canonical_name": "Relationship Coach",
        "target_url": action_target["url"],
        "business_email": "Collabs@relationshipcoach.example",
        "contact_evidence": {
            "provenance_type": "PUBLIC_BUSINESS_SOURCE",
            "source_url": "https://relationshipcoach.example/contact",
            "source_label": "Creator business contact page",
            "source_excerpt": "Partnerships: Collabs@relationshipcoach.example",
            "observed_at": datetime.now(UTC).isoformat(),
        },
        "relevance_rationale": (
            "The creator publishes relationship-advice content aligned with Oracle's use case."
        ),
        "icp_overlap_rationale": "Audience seeks relationship clarity and reflective content.",
        "confidence": 82,
        "language": "English",
        "jurisdiction": "US",
    }


@pytest.mark.asyncio
async def test_public_business_contact_requires_exact_evidence_and_known_action_target(
    monkeypatch,
) -> None:
    product_id = _confirmed_product()
    opportunity = await _enriched_instagram(monkeypatch, product_id)

    response = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json=_public_payload(opportunity),
    )

    assert response.status_code == 201
    target = response.json()
    assert target["product_id"] == product_id
    assert target["opportunity_id"] == opportunity["id"]
    assert target["target_url"] == "https://www.instagram.com/reel/ABC123/"
    assert target["business_email"] == "Collabs@relationshipcoach.example"
    assert target["contact_key"] == "collabs@relationshipcoach.example"
    assert target["contact_evidence"]["provenance_type"] == "PUBLIC_BUSINESS_SOURCE"
    assert target["contact_evidence"]["source_url"] == "https://relationshipcoach.example/contact"
    assert target["status"] == "ACTIVE"

    bad_evidence = _public_payload(opportunity)
    bad_evidence["business_email"] = "other@relationshipcoach.example"
    bad = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json=bad_evidence,
    )
    assert bad.status_code == 409
    assert "exact business_email" in bad.json()["detail"]

    unknown_target = _public_payload(opportunity)
    unknown_target["business_email"] = "another@relationshipcoach.example"
    unknown_target["contact_evidence"]["source_excerpt"] = (
        "Partnerships: another@relationshipcoach.example"
    )
    unknown_target["target_url"] = "https://www.instagram.com/reel/NOT_DISCOVERED/"
    bad_url = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json=unknown_target,
    )
    assert bad_url.status_code == 409
    assert "must match the DistributionOpportunity" in bad_url.json()["detail"]


@pytest.mark.asyncio
async def test_operator_supplied_contact_is_explicit_and_cannot_fake_public_provenance(
    monkeypatch,
) -> None:
    product_id = _confirmed_product()
    opportunity = await _enriched_instagram(monkeypatch, product_id)
    action_target = opportunity["metadata"]["enrichment"]["action_targets"][0]

    payload = _public_payload(opportunity)
    payload["business_email"] = "founder@partner.example"
    payload["target_type"] = "PARTNER"
    payload["target_url"] = action_target["url"]
    payload["contact_evidence"] = {
        "provenance_type": "OPERATOR_SUPPLIED",
        "source_label": "Provided directly by the operator",
    }
    response = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json=payload,
    )
    assert response.status_code == 201
    assert response.json()["contact_evidence"]["source_url"] is None

    payload["business_email"] = "second@partner.example"
    payload["contact_evidence"]["source_url"] = "https://partner.example/contact"
    fake_public = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json=payload,
    )
    assert fake_public.status_code == 422
    assert "must not be represented as public-source evidence" in str(fake_public.json())


@pytest.mark.asyncio
async def test_duplicate_contact_for_same_opportunity_is_rejected(monkeypatch) -> None:
    product_id = _confirmed_product()
    opportunity = await _enriched_instagram(monkeypatch, product_id)
    payload = _public_payload(opportunity)

    first = client.post(f"/v1/products/{product_id}/outreach-targets", json=payload)
    second = client.post(f"/v1/products/{product_id}/outreach-targets", json=payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert "already exists" in second.json()["detail"]


@pytest.mark.asyncio
async def test_suppression_is_persisted_and_blocks_execution(monkeypatch) -> None:
    product_id = _confirmed_product()
    opportunity = await _enriched_instagram(monkeypatch, product_id)
    created = client.post(
        f"/v1/products/{product_id}/outreach-targets",
        json=_public_payload(opportunity),
    ).json()

    response = client.post(
        f"/v1/outreach-targets/{created['id']}/suppress",
        json={"reason": "OPT_OUT", "note": "Recipient asked not to be contacted again."},
    )

    assert response.status_code == 200
    target = response.json()
    assert target["status"] == "SUPPRESSED"
    assert target["suppression"]["reason"] == "OPT_OUT"
    assert "Recipient asked" in target["suppression"]["note"]

    with pytest.raises(ValueError, match="OPT_OUT"):
        outreach_target_service.require_executable(target["id"])

    repeated = client.post(
        f"/v1/outreach-targets/{created['id']}/suppress",
        json={"reason": "OPERATOR_SUPPRESSED"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["suppression"]["reason"] == "OPT_OUT"


@pytest.mark.asyncio
async def test_business_email_validation_is_conservative(monkeypatch) -> None:
    product_id = _confirmed_product()
    opportunity = await _enriched_instagram(monkeypatch, product_id)
    payload = _public_payload(opportunity)

    for invalid in (
        "person @partner.example",
        "person@@partner.example",
        ".person@partner.example",
        "person@localhost",
        "person@-partner.example",
    ):
        payload["business_email"] = invalid
        payload["contact_evidence"]["source_excerpt"] = f"Business: {invalid}"
        response = client.post(
            f"/v1/products/{product_id}/outreach-targets",
            json=payload,
        )
        assert response.status_code in {409, 422}


def test_outreach_target_routes_are_operator_authenticated_in_production(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPERATOR_AUTH_REQUIRED", "true")
    monkeypatch.setenv("OPERATOR_API_KEY", "test-operator-secret")

    from app.config import get_settings

    get_settings.cache_clear()
    try:
        response = client.get(
            f"/v1/products/{'0' * 8}-0000-0000-0000-000000000000/outreach-targets"
        )
        assert response.status_code == 401
    finally:
        get_settings.cache_clear()
