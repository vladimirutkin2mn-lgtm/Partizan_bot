from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
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
from app.distribution_play_schemas import DistributionPlayView
from app.distribution_schemas import DistributionOpportunityView
from app.distribution_types import DistributionPlatform
from app.main import app
from app.runtime_store import MemoryRuntimeStateStore

PROJECT_ID = UUID("88888888-8888-4888-8888-888888888888")
PRODUCT_ID = UUID("99999999-9999-4999-8999-999999999999")
ICP_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OPPORTUNITY_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
PLAY_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
SOURCE_URL = "https://www.reddit.com/r/freelance/comments/example/thread/"


def _draft() -> CustomerStartingMoveDraftView:
    return CustomerStartingMoveDraftView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        review_status="ACCEPTED",
        source_title="Freelancer bookkeeping discussion",
        source_url=SOURCE_URL,
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


def _play() -> DistributionPlayView:
    return DistributionPlayView(
        id=PLAY_ID,
        product_id=PRODUCT_ID,
        icp_id=ICP_ID,
        opportunity_id=OPPORTUNITY_ID,
        platform=DistributionPlatform.REDDIT,
        opportunity_kind="SUBREDDIT",
        opportunity_title="Freelancer bookkeeping discussion",
        tactic_id="community_helpful_reply",
        tactic_class="COMMUNITY",
        action_type="REPLY",
        automation_level="ASSISTED",
        attribution_level="ACTION",
        identity_required=False,
        community_policy_required=False,
        status="READY",
        blockers=[],
        hypothesis="A useful bookkeeping reply will generate qualified downstream interest.",
        execution_steps=["Read the thread context", "Prepare a useful native reply"],
        success_metric="Qualified downstream interest",
        estimated_cost_min=0,
        estimated_cost_max=0,
        effort_hours=0.5,
        time_to_signal_days=3,
        priority_score=80,
        rationale=["Matches researched customer pain"],
    )


def _opportunity() -> DistributionOpportunityView:
    return DistributionOpportunityView(
        id=OPPORTUNITY_ID,
        icp_id=ICP_ID,
        platform=DistributionPlatform.REDDIT,
        kind="SUBREDDIT",
        canonical_key="reddit:r/freelance:bookkeeping",
        title="Freelancer bookkeeping discussion",
        url=SOURCE_URL,
        relevance_score=92,
        rationale="Exact researched source",
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


def test_operator_preparation_link_requires_auth_and_explicit_true(monkeypatch) -> None:
    service = CustomerExecutionRequestService(MemoryRuntimeStateStore())
    request = service.request(
        project={"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)},
        draft=_draft(),
        setup=_setup(),
    )
    play = _play()
    opportunity = _opportunity()
    monkeypatch.setattr(route_module, "customer_execution_request_service", service)
    monkeypatch.setattr(
        route_module,
        "distribution_play_service",
        SimpleNamespace(find=lambda _product_id, _play_id: play),
    )
    monkeypatch.setattr(
        route_module,
        "audience_intelligence_service",
        SimpleNamespace(find_opportunity=lambda _opportunity_id: opportunity),
    )
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="production",
        operator_api_key="operator-secret",
    )
    client = TestClient(app)
    path = f"/v1/customer-execution-requests/{request.id}/preparation-link"
    headers = {"X-Partizan-Operator-Key": "operator-secret"}
    try:
        missing_auth = client.post(
            path,
            json={"distribution_play_id": str(PLAY_ID), "confirm_link": True},
        )
        false_confirmation = client.post(
            path,
            headers=headers,
            json={"distribution_play_id": str(PLAY_ID), "confirm_link": False},
        )
        allowed = client.post(
            path,
            headers=headers,
            json={"distribution_play_id": str(PLAY_ID), "confirm_link": True},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert missing_auth.status_code == 401
    assert false_confirmation.status_code == 422
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "PREPARATION_READY"
    assert allowed.json()["distribution_play_id"] == str(PLAY_ID)
    assert allowed.json()["opportunity_id"] == str(OPPORTUNITY_ID)
    assert allowed.json()["execution_allowed"] is False
    assert allowed.json()["customer_publish_confirmation_required"] is True


def test_operator_action_prepare_requires_auth_and_explicit_true(monkeypatch) -> None:
    service = CustomerExecutionRequestService(MemoryRuntimeStateStore())
    request = service.request(
        project={"id": str(PROJECT_ID), "product_id": str(PRODUCT_ID)},
        draft=_draft(),
        setup=_setup(),
    )
    service.link_preparation(
        request_id=request.id,
        play=_play(),
        opportunity=_opportunity(),
    )
    monkeypatch.setattr(route_module, "customer_execution_request_service", service)
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="production",
        operator_api_key="operator-secret",
    )
    client = TestClient(app)
    path = f"/v1/customer-execution-requests/{request.id}/prepare-action"
    headers = {"X-Partizan-Operator-Key": "operator-secret"}
    try:
        missing_auth = client.post(path, json={"confirm_prepare": True})
        false_confirmation = client.post(
            path,
            headers=headers,
            json={"confirm_prepare": False},
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert missing_auth.status_code == 401
    assert false_confirmation.status_code == 422
    assert service.get_request(request.id).status == "PREPARATION_READY"


def test_preparation_link_remains_non_executing_and_prepare_is_separate() -> None:
    source = Path("app/customer_execution_request_routes.py").read_text(encoding="utf-8")
    link_start = source.index("def link_customer_execution_preparation(")
    prepare_start = source.index("def prepare_customer_execution_action(")
    link_block = source[link_start:prepare_start]

    assert "distribution_execution_service.prepare" not in link_block
    assert "/prepare-action" in source
    assert "distribution_execution_service.prepare" in source
    assert "/approve" not in source
    assert "/mark-executed" not in source
    assert "/publish" not in source
