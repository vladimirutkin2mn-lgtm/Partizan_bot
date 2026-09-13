from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

import app.customer_execution_request_routes as route_module
from app.channel_execution import PublisherMode
from app.config import Settings, get_settings
from app.customer_channel_schemas import (
    CustomerStartingMoveDraftView,
    CustomerStartingMoveSetupView,
)
from app.customer_execution_requests import CustomerExecutionRequestService
from app.distribution_types import DistributionPlatform
from app.main import app
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("88888888-8888-4888-8888-888888888888")
PRODUCT_ID = UUID("99999999-9999-4999-8999-999999999999")


def _draft() -> CustomerStartingMoveDraftView:
    return CustomerStartingMoveDraftView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        review_status="ACCEPTED",
        source_title="Freelancer bookkeeping discussion",
        source_url="https://www.reddit.com/r/freelance/comments/example/thread/",
        title="Useful bookkeeping reply",
        context_text="Freelancers are comparing recurring bookkeeping workflow pain.",
        content_text="Share a useful bookkeeping workflow perspective without a product link.",
        rationale="Grounded in researched evidence.",
        signal_to_watch="Useful replies and downstream interest",
        execution_allowed=False,
        execution_requirement="Accepted for setup only; execution remains separate.",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _setup() -> CustomerStartingMoveSetupView:
    return CustomerStartingMoveSetupView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        state="READY_FOR_HANDOFF",
        channel_mode="RESEARCH_ONLY",
        publisher_mode=PublisherMode.MANUAL,
        connected=False,
        execution_allowed=False,
        next_step="Manual handoff is ready.",
    )


def test_customer_execution_request_requires_customer_session() -> None:
    response = TestClient(app).get(
        f"/customer/workspace/{uuid4()}/starting-move/execution-request"
    )

    assert response.status_code == 401


def test_customer_request_requires_explicit_true_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        route_module,
        "_request_context",
        lambda _session, _project_id: (
            {"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)},
            _draft(),
            _setup(),
        ),
    )

    response = TestClient(app).post(
        f"/customer/workspace/{PROJECT_ID}/starting-move/execution-request",
        json={"confirm_request": False},
    )

    assert response.status_code == 422


def test_customer_request_creates_only_non_executing_queue_record(monkeypatch) -> None:
    service = CustomerExecutionRequestService(MemoryRuntimeStateStore())
    monkeypatch.setattr(route_module, "customer_execution_request_service", service)
    monkeypatch.setattr(
        route_module,
        "_request_context",
        lambda _session, _project_id: (
            {"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)},
            _draft(),
            _setup(),
        ),
    )

    response = TestClient(app).post(
        f"/customer/workspace/{PROJECT_ID}/starting-move/execution-request",
        json={"confirm_request": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "REQUESTED"
    assert payload["execution_allowed"] is False
    assert payload["customer_publish_confirmation_required"] is True
    assert payload["platform"] == "REDDIT"
    assert len(service.list_requests()) == 1


def test_operator_queue_requires_operator_auth(monkeypatch) -> None:
    service = CustomerExecutionRequestService(MemoryRuntimeStateStore())
    service.request(
        project={"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)},
        draft=_draft(),
        setup=_setup(),
    )
    monkeypatch.setattr(route_module, "customer_execution_request_service", service)
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="production",
        operator_api_key="operator-secret",
    )
    client = TestClient(app)
    try:
        missing = client.get("/v1/customer-execution-requests")
        allowed = client.get(
            "/v1/customer-execution-requests",
            headers={"X-Partizan-Operator-Key": "operator-secret"},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert missing.status_code == 401
    assert allowed.status_code == 200
    assert len(allowed.json()) == 1
    assert allowed.json()[0]["execution_allowed"] is False
