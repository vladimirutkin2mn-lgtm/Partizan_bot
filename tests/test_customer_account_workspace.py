from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.customer_account import (
    CUSTOMER_ACCOUNT_EMAIL_NAMESPACE,
    CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE,
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    customer_account_service,
)
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store


@pytest.fixture(autouse=True)
def reset_customer_account_state() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()


def _preview():
    return customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )


def _register(client: TestClient, *, email: str = "founder@example.com"):
    preview = _preview()
    response = client.post(
        "/customer/account/register",
        json={
            "email": email,
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    return preview, response


def test_registration_claims_project_rotates_browser_token_and_sets_http_only_session() -> None:
    client = TestClient(app)
    preview, response = _register(client)

    assert response.status_code == 200
    payload = response.json()
    assert payload["email"] == "founder@example.com"
    assert payload["projects"][0]["project_id"] == str(preview.project_id)
    cookie = response.headers["set-cookie"]
    assert f"{CUSTOMER_ACCOUNT_SESSION_COOKIE}=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie

    old_token = client.get(
        f"/v1/customer-projects/{preview.project_id}",
        headers={"X-Partizan-Customer-Token": preview.customer_token},
    )
    assert old_token.status_code == 401

    workspace = client.get(f"/customer/workspace/{preview.project_id}")
    assert workspace.status_code == 200
    assert workspace.json()["project"]["project_id"] == str(preview.project_id)
    assert workspace.json()["account"]["email"] == "founder@example.com"
    assert workspace.json()["preview_directions"]
    assert workspace.json()["preview_directions"][0]["name"] == preview.directions[0].name
    assert workspace.json()["preview_directions"][0]["rationale"] == preview.directions[0].rationale


def test_registration_retry_with_same_credentials_resumes_existing_account() -> None:
    first_browser = TestClient(app)
    preview, created = _register(first_browser)
    assert created.status_code == 200
    account_id = created.json()["account_id"]

    retry_browser = TestClient(app)
    retried = retry_browser.post(
        "/customer/account/register",
        json={
            "email": "FOUNDER@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )

    assert retried.status_code == 200
    assert retried.json()["account_id"] == account_id
    assert retried.json()["projects"][0]["project_id"] == str(preview.project_id)
    assert retry_browser.get(f"/customer/workspace/{preview.project_id}").status_code == 200


def test_registration_with_existing_credentials_can_claim_a_new_project() -> None:
    first_browser = TestClient(app)
    first_preview, created = _register(first_browser)
    assert created.status_code == 200
    second_preview = _preview()

    second_browser = TestClient(app)
    resumed = second_browser.post(
        "/customer/account/register",
        json={
            "email": "founder@example.com",
            "password": "correct-horse-42",
            "project_id": str(second_preview.project_id),
            "customer_token": second_preview.customer_token,
        },
    )

    assert resumed.status_code == 200
    project_ids = {item["project_id"] for item in resumed.json()["projects"]}
    assert project_ids == {str(first_preview.project_id), str(second_preview.project_id)}
    assert second_browser.get(f"/customer/workspace/{second_preview.project_id}").status_code == 200


def test_registration_existing_email_with_wrong_password_does_not_claim_project() -> None:
    owner = TestClient(app)
    _, created = _register(owner)
    assert created.status_code == 200
    candidate = _preview()

    stranger = TestClient(app)
    rejected = stranger.post(
        "/customer/account/register",
        json={
            "email": "founder@example.com",
            "password": "definitely-wrong",
            "project_id": str(candidate.project_id),
            "customer_token": candidate.customer_token,
        },
    )

    assert rejected.status_code == 409
    project = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, str(candidate.project_id))
    assert project is not None
    assert not project.get("customer_account_id")


def test_registration_repairs_stale_email_index() -> None:
    store = get_runtime_store()
    store.put(
        CUSTOMER_ACCOUNT_EMAIL_NAMESPACE,
        "founder@example.com",
        {"account_id": "missing-account"},
    )

    client = TestClient(app)
    _, created = _register(client)

    assert created.status_code == 200
    repaired = store.get(CUSTOMER_ACCOUNT_EMAIL_NAMESPACE, "founder@example.com")
    assert repaired is not None
    assert repaired["account_id"] == created.json()["account_id"]


def test_account_login_restores_workspace_from_another_browser() -> None:
    first_browser = TestClient(app)
    preview, created = _register(first_browser)
    assert created.status_code == 200

    second_browser = TestClient(app)
    login = second_browser.post(
        "/customer/account/login",
        json={"email": "FOUNDER@example.com", "password": "correct-horse-42"},
    )
    assert login.status_code == 200

    workspace = second_browser.get(f"/customer/workspace/{preview.project_id}")
    assert workspace.status_code == 200
    assert workspace.json()["project"]["brief"].startswith("AI bookkeeping assistant")


def test_wrong_password_and_unauthenticated_workspace_fail_closed() -> None:
    owner = TestClient(app)
    preview, created = _register(owner)
    assert created.status_code == 200

    stranger = TestClient(app)
    assert stranger.get(f"/customer/workspace/{preview.project_id}").status_code == 401
    bad_login = stranger.post(
        "/customer/account/login",
        json={"email": "founder@example.com", "password": "definitely-wrong"},
    )
    assert bad_login.status_code == 401


def test_project_cannot_be_claimed_by_a_second_account() -> None:
    owner = TestClient(app)
    preview, created = _register(owner)
    assert created.status_code == 200

    second_project = _preview()
    second = TestClient(app)
    second_registration = second.post(
        "/customer/account/register",
        json={
            "email": "other@example.com",
            "password": "another-safe-password",
            "project_id": str(second_project.project_id),
            "customer_token": second_project.customer_token,
        },
    )
    assert second_registration.status_code == 200

    claim = second.post(
        "/customer/account/projects/claim",
        json={
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert claim.status_code in {403, 409}


def test_project_claim_reservation_blocks_competing_account_before_token_rotation() -> None:
    owner = TestClient(app)
    _, created = _register(owner)
    assert created.status_code == 200
    candidate = _preview()
    store = get_runtime_store()
    store.put(
        CUSTOMER_ACCOUNT_PROJECT_CLAIM_NAMESPACE,
        str(candidate.project_id),
        {
            "account_id": "competing-account",
            "project_id": str(candidate.project_id),
        },
    )

    claim = owner.post(
        "/customer/account/projects/claim",
        json={
            "project_id": str(candidate.project_id),
            "customer_token": candidate.customer_token,
        },
    )

    assert claim.status_code == 409
    assert "already being claimed" in claim.json()["detail"]
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(candidate.project_id))
    assert project is not None
    assert not project.get("customer_account_id")
    still_browser_owned = owner.get(
        f"/v1/customer-projects/{candidate.project_id}",
        headers={"X-Partizan-Customer-Token": candidate.customer_token},
    )
    assert still_browser_owned.status_code == 200


def test_logout_revokes_current_session() -> None:
    client = TestClient(app)
    preview, created = _register(client)
    assert created.status_code == 200
    assert client.get(f"/customer/workspace/{preview.project_id}").status_code == 200

    logout = client.post("/customer/account/logout")
    assert logout.status_code == 204
    assert client.get(f"/customer/workspace/{preview.project_id}").status_code == 401


def test_customer_workspace_page_is_separate_from_internal_operator_app() -> None:
    client = TestClient(app)
    page = client.get("/workspace")
    javascript = client.get("/workspace/assets/workspace.v1.js")
    css = client.get("/workspace/assets/workspace.v1.css")

    assert page.status_code == 200
    assert "Partizan Workspace" in page.text
    assert "Acquisition budget" in page.text
    assert "Current work" in page.text
    assert "Account access" in page.text
    assert "What's the most you'd pay for one new customer?" in page.text
    assert "Let's find your first users." in page.text
    assert "dogfooding workspace" not in page.text
    assert "/app/assets/" not in page.text
    assert javascript.status_code == 200
    assert "/customer/workspace/" in javascript.text
    assert "X-Partizan-Customer-Token" not in javascript.text
    assert css.status_code == 200
