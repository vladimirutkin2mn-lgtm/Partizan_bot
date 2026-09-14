from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

import app.customer_execution_request_routes as route_module
from app.customer_execution_request_schemas import CustomerPreparedActionView
from app.distribution_types import DistributionPlatform
from app.main import app

PROJECT_ID = UUID("77777777-7777-4777-8777-777777777777")
REQUEST_ID = UUID("88888888-8888-4888-8888-888888888888")
ACTION_ID = UUID("99999999-9999-4999-8999-999999999999")
CREATIVE_ASSET_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
SOURCE_URL = "https://www.reddit.com/r/freelance/comments/example/thread/"


def _prepared(*, confirmed: bool = False) -> CustomerPreparedActionView:
    return CustomerPreparedActionView(
        request_id=REQUEST_ID,
        project_id=PROJECT_ID,
        distribution_action_id=ACTION_ID,
        platform=DistributionPlatform.REDDIT,
        source_title="Freelancer bookkeeping discussion",
        source_url=SOURCE_URL,
        target_url=SOURCE_URL,
        draft_title="Useful bookkeeping reply",
        context_text="Freelancers are comparing recurring bookkeeping workflow pain.",
        content_text="Share a useful bookkeeping workflow perspective without a product link.",
        customer_publish_confirmed=confirmed,
        customer_publish_confirmed_at=datetime.now(UTC) if confirmed else None,
        execution_allowed=False,
        operator_approval_required=True,
        published=False,
    )


def test_prepared_action_route_requires_customer_session() -> None:
    response = TestClient(app).get(
        f"/customer/workspace/{uuid4()}/starting-move/execution-request/prepared-action"
    )

    assert response.status_code == 401


def test_confirmation_requires_literal_true_and_rejects_extra_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        route_module,
        "_request_context",
        lambda _session, _project_id: ({"id": str(PROJECT_ID)}, object(), None),
    )
    client = TestClient(app)
    path = f"/customer/workspace/{PROJECT_ID}/starting-move/execution-request/confirmation"

    false_confirmation = client.post(path, json={"confirm_publish": False})
    injected_action = client.post(
        path,
        json={"confirm_publish": True, "distribution_action_id": str(ACTION_ID)},
    )

    assert false_confirmation.status_code == 422
    assert injected_action.status_code == 422


def test_confirmation_endpoint_returns_only_prepared_non_published_action(monkeypatch) -> None:
    prepared = _prepared(confirmed=True)
    calls = []
    monkeypatch.setattr(
        route_module,
        "_request_context",
        lambda _session, _project_id: ({"id": str(PROJECT_ID)}, object(), None),
    )
    monkeypatch.setattr(
        route_module,
        "customer_publish_confirmation_service",
        SimpleNamespace(
            confirm=lambda **kwargs: calls.append(kwargs) or prepared,
            view=lambda **_kwargs: prepared,
        ),
    )
    client = TestClient(app)
    path = f"/customer/workspace/{PROJECT_ID}/starting-move/execution-request/confirmation"

    response = client.post(path, json={"confirm_publish": True})

    assert response.status_code == 200
    payload = response.json()
    assert payload["distribution_action_id"] == str(ACTION_ID)
    assert payload["action_status"] == "PREPARED"
    assert payload["customer_publish_confirmed"] is True
    assert payload["execution_allowed"] is False
    assert payload["operator_approval_required"] is True
    assert payload["published"] is False
    assert len(calls) == 1
    assert set(calls[0]) == {"project", "draft", "creative_asset_id"}
    assert calls[0]["creative_asset_id"] is None


def test_confirmation_endpoint_forwards_exact_reviewed_creative_asset_id(monkeypatch) -> None:
    prepared = _prepared(confirmed=True)
    calls = []
    monkeypatch.setattr(
        route_module,
        "_request_context",
        lambda _session, _project_id: ({"id": str(PROJECT_ID)}, object(), None),
    )
    monkeypatch.setattr(
        route_module,
        "customer_publish_confirmation_service",
        SimpleNamespace(
            confirm=lambda **kwargs: calls.append(kwargs) or prepared,
            view=lambda **_kwargs: prepared,
        ),
    )
    client = TestClient(app)
    path = f"/customer/workspace/{PROJECT_ID}/starting-move/execution-request/confirmation"

    response = client.post(
        path,
        json={
            "confirm_publish": True,
            "creative_asset_id": str(CREATIVE_ASSET_ID),
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    assert calls[0]["creative_asset_id"] == CREATIVE_ASSET_ID
