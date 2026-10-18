"""Owner-only API coverage for read-only customer Telegram discovery progress."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.autonomous_opportunity_refresh import AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE
from app.customer_account import customer_account_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store
from app.telegram_discovery_stage_queue import (
    TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE,
    TELEGRAM_DISCOVERY_RETENTION_NAMESPACE,
    TELEGRAM_DISCOVERY_STAGE_NAMESPACE,
    TelegramDiscoveryStageQueue,
)


@pytest.fixture(autouse=True)
def reset_state() -> None:
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()
    store = get_runtime_store()
    for namespace in (
        AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
        TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE,
        TELEGRAM_DISCOVERY_RETENTION_NAMESPACE,
        TELEGRAM_DISCOVERY_STAGE_NAMESPACE,
    ):
        store.clear_namespace(namespace)


def _register(client: TestClient, email: str):
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="A clearly defined niche product and audience.",
            website_url="https://example.com",
            market="United States",
            goal="Find qualified customers",
            budget_usd=200,
        )
    )
    result = client.post(
        "/customer/account/register",
        json={
            "email": email,
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert result.status_code == 200
    return preview


def _product_for(project_id):
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    product_id = uuid4()
    project["product_id"] = str(product_id)
    store.put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)
    return product_id


def test_owner_only_read_and_no_cross_account_stage_data():
    owner, intruder = TestClient(app), TestClient(app)
    project = _register(owner, "diag-owner@example.com")
    other = _register(intruder, "diag-other@example.com")
    product = _product_for(project.project_id)
    _product_for(other.project_id)
    queue = TelegramDiscoveryStageQueue(get_runtime_store(), product, run_id="private-run")
    queue.start()
    queue.mark(
        "registry", "SKIPPED_NO_CANDIDATES", reason="EMPTY_REGISTRY",
        source="REGISTRY_NATIVE_REFRESH",
    )
    queue.mark(
        "native", "NO_FIT", reason="NO_FIT_MATCH",
        source="TELETHON_NATIVE", query="FemDom discussion groups",
        candidate_count=2,
    )
    queue.mark(
        "catalog", "SKIPPED_DISABLED", reason="DISABLED",
        source="TGSTAT_PUBLIC",
    )
    queue.mark(
        "paid", "BLOCKED", reason="NATIVE_SEARCH_NOT_COMPLETED",
        source="OPENAI_WEB", query="site:t.me FemDom",
    )
    next_check = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
    get_runtime_store().put(
        AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE,
        str(product),
        {
            "status": "REFRESHED", "next_attempt_at": next_check,
            "next_attempt_reason": "NORMAL_DISCOVERY_INTERVAL",
        },
    )

    url = f"/customer/workspace/{project.project_id}/telegram-discovery"
    response = owner.get(url)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETED"
    assert data["last_run_complete"] is True
    assert datetime.fromisoformat(data["next_refresh_at"].replace("Z", "+00:00")) == (
        datetime.fromisoformat(next_check)
    )
    assert len(data["stages"]) == 4
    by_stage = {item["stage"]: item for item in data["stages"]}
    assert by_stage["native"]["source"] == "Telegram"
    assert by_stage["native"]["search_query"] == "FemDom discussion groups"
    assert by_stage["native"]["candidate_count"] == 2
    assert by_stage["paid"]["status"] == "BLOCKED"
    assert "billing" not in str(data).casefold()
    assert "session" not in str(data).casefold()
    assert "publisher" not in str(data).casefold()
    assert "private-run" not in str(data)

    assert TestClient(app).get(url).status_code == 401
    assert intruder.get(url).status_code == 403


def test_no_run_and_unmapped_project_are_graceful_not_fake_success():
    client = TestClient(app)
    project = _register(client, "diag-empty@example.com")
    url = f"/customer/workspace/{project.project_id}/telegram-discovery"
    before = client.get(url)
    assert before.status_code == 200
    assert before.json()["status"] == "NOT_RUN"
    assert before.json()["stages"] == []
    product = _product_for(project.project_id)
    after = client.get(url)
    assert after.status_code == 200
    assert after.json()["status"] == "NOT_RUN"
    assert after.json()["stages"] == []
    # Ensure a normal request does not schedule discovery or reserve paid spend.
    assert get_runtime_store().get(TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE, str(product)) is None


def test_partial_run_has_pending_stages_and_unknown_reason_is_redacted():
    client = TestClient(app)
    project = _register(client, "diag-partial@example.com")
    product = _product_for(project.project_id)
    queue = TelegramDiscoveryStageQueue(get_runtime_store(), product)
    queue.start()
    queue.mark(
        "native", "FAILED_RETRYABLE",
        reason="Internal URL https://secret.example/private?token=abc",
        error_type="SecretProviderError",
        query="<script>alert(1)</script>",
        source="TELETHON_NATIVE",
    )
    result = client.get(
        f"/customer/workspace/{project.project_id}/telegram-discovery"
    )
    assert result.status_code == 200
    data = result.json()
    assert data["status"] == "IN_PROGRESS"
    native = next(item for item in data["stages"] if item["stage"] == "native")
    assert native["detail"] == "Research encountered a temporary error"
    assert "secret.example" not in str(data)
    assert "SecretProviderError" not in str(data)
    assert native["search_query"] == "<script>alert(1)</script>"
    assert any(item["status"] == "PENDING" for item in data["stages"])
