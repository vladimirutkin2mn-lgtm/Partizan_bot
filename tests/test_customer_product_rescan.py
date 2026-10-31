"""Existing workspace product rescan: explicit review, account scope, history."""
from fastapi.testclient import TestClient

from app.customer_account import customer_account_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_product_rescan import HISTORY_NAMESPACE, PROPOSAL_NAMESPACE
from app.main import app
from app.product_source import ProductSourceContext, ProductSourceType
from app.runtime_store import get_runtime_store

PROJECT = {
    "name": "Existing community",
    "project_type": "TELEGRAM_COMMUNITY",
    "reference_url": "https://t.me/+inviteForTesting",
    "brief": "An invitation-only discussion group with unknown visitor value.",
    "market": "United States",
    "goal": "Grow subscribers",
    "budget_usd": 10,
}


def account_client(email: str = "rescan@example.com") -> TestClient:
    customer_account_service.reset()
    customer_funnel_service.reset()
    client = TestClient(app)
    from app.customer_schemas import CustomerPreviewRequest

    preview = customer_funnel_service.create_preview(CustomerPreviewRequest(
        brief="Project used to register a test account with a confirmed owner.",
        market="US", goal="Grow audience", budget_usd=10,
    ))
    registered = client.post("/customer/account/register", json={
        "email": email, "password": "product-rescan-test-password-42",
        "project_id": str(preview.project_id), "customer_token": preview.customer_token,
    })
    assert registered.status_code == 200
    return client


def new_project(client: TestClient) -> str:
    response = client.post("/customer/account/projects", json=PROJECT)
    assert response.status_code == 201, response.text
    return response.json()["project_id"]


def approval(scan_id: str, **changes) -> dict:
    payload = {
        "scan_id": scan_id,
        "name": "Existing community",
        "description": "A small invitation-only community for discussing local cultural events.",
        "value_proposition": "Members share verified local event announcements and talk about plans.",
        "known_audience": "Adults interested in local cultural events and meeting people.",
    }
    payload.update(changes)
    return payload


def test_rescan_invite_is_review_only_and_confirmation_creates_product_without_calls():
    client = account_client()
    project_id = new_project(client)
    store = get_runtime_store()
    before = store.get(CUSTOMER_PROJECT_NAMESPACE, project_id)
    assert before["product_id"] is None
    scan = client.post(f"/customer/account/projects/{project_id}/product/rescan")
    assert scan.status_code == 200, scan.text
    draft = scan.json()
    assert draft["status"] == "PENDING"
    assert draft["source_status"] == "INSUFFICIENT_PUBLIC_EVIDENCE"
    assert draft["proposed"]["value_proposition"] == ""
    assert store.get(CUSTOMER_PROJECT_NAMESPACE, project_id) == before
    assert store.get(PROPOSAL_NAMESPACE, project_id)["status"] == "PENDING"
    assert client.get(
        f"/customer/account/projects/{project_id}/product/rescan"
    ).json()["pending"]["scan_id"] == draft["scan_id"]
    assert client.post(
        f"/customer/account/projects/{project_id}/product/rescan/confirm",
        json=approval(draft["scan_id"], value_proposition=" "),
    ).status_code == 422
    assert store.get(CUSTOMER_PROJECT_NAMESPACE, project_id) == before

    confirmed = client.post(
        f"/customer/account/projects/{project_id}/product/rescan/confirm",
        json=approval(draft["scan_id"]),
    )
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()
    assert result["voice_refresh_status"] == "REVIEW_REQUIRED"
    updated = store.get(CUSTOMER_PROJECT_NAMESPACE, project_id)
    assert updated["product_id"] == result["product_id"]
    assert updated["brief"] == approval(draft["scan_id"])["description"]
    assert updated["budget_usd"] == 10
    product = store.get("product_intake", result["product_id"])["product"]
    assert product["value_proposition"] == approval(draft["scan_id"])["value_proposition"]
    assert product["known_audience"] == [approval(draft["scan_id"])["known_audience"]]
    assert product["status"] == "CONFIRMED"
    history = store.get(HISTORY_NAMESPACE, f"{project_id}:{draft['scan_id']}")
    assert history["old_product"] is None
    assert history["new_product"] == product
    assert client.post(
        f"/customer/account/projects/{project_id}/product/rescan/confirm",
        json=approval(draft["scan_id"]),
    ).status_code == 409


def test_second_rescan_preserves_original_and_rejects_stale_draft():
    client = account_client()
    project_id = new_project(client)
    url = f"/customer/account/projects/{project_id}/product/rescan"
    first = client.post(url).json()
    assert client.post(url + "/confirm", json=approval(first["scan_id"])).status_code == 200
    store = get_runtime_store()
    product_id = store.get(CUSTOMER_PROJECT_NAMESPACE, project_id)["product_id"]
    original = store.get("product_intake", product_id)["product"]

    second = client.post(url).json()
    assert second["proposed"]["value_proposition"] == original["value_proposition"]
    assert second["proposed"]["known_audience"] == original["known_audience"][0]
    rejected = client.post(
        url + "/confirm",
        json=approval(first["scan_id"], value_proposition="New example of member value"),
    )
    assert rejected.status_code == 409
    assert store.get("product_intake", product_id)["product"] == original

    updated = client.post(
        url + "/confirm",
        json=approval(
            second["scan_id"],
            value_proposition="Members can discover local events and compare real experiences.",
        ),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["product_id"] == product_id
    assert store.get(HISTORY_NAMESPACE, f"{project_id}:{second['scan_id']}")["old_product"] == original


def test_rescan_cross_account_and_anonymous_are_rejected():
    client = account_client()
    project_id = new_project(client)
    client.post("/customer/account/logout")
    assert client.post(f"/customer/account/projects/{project_id}/product/rescan").status_code == 401
    other = account_client(email="other-rescan@example.com")
    assert other.get(f"/customer/account/projects/{project_id}/product/rescan").status_code == 403
    assert other.post(f"/customer/account/projects/{project_id}/product/rescan").status_code == 403


def test_rescan_public_source_prepares_evidence_without_auto_confirm(monkeypatch):
    async def public_source(_link):
        return ProductSourceContext(
            source_type=ProductSourceType.WEBSITE,
            source_label="Website",
            link="https://example.com",
            title="Arts community",
            description="A curated membership community with weekly introductions to events and creators.",
            text="Local events, active moderation and weekly member meetups.",
            needs_founder_context=False,
            clarification_question=None,
        )

    monkeypatch.setattr(
        "app.customer_product_rescan.read_public_product_source", public_source,
    )
    client = account_client()
    project_id = new_project(client)
    project = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, project_id)
    draft = client.post(
        f"/customer/account/projects/{project_id}/product/rescan"
    ).json()
    assert draft["source_status"] == "PUBLIC_SOURCE_READ"
    assert draft["proposed"]["description"].startswith("A curated membership")
    assert not draft["proposed"]["value_proposition"]
    assert get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, project_id) == project
