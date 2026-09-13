from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

import app.customer_channel_selection_routes as route_module
from app.channel_execution import ChannelCapability, PublisherMode
from app.customer_channel_schemas import (
    CustomerChannelCapabilityView,
    CustomerChannelView,
    CustomerStartingMoveDraftView,
)
from app.distribution_types import DistributionPlatform
from app.main import app

PROJECT_ID = UUID("55555555-5555-4555-8555-555555555555")


def _accepted_draft() -> CustomerStartingMoveDraftView:
    return CustomerStartingMoveDraftView(
        project_id=PROJECT_ID,
        platform=DistributionPlatform.REDDIT,
        channel_label="Reddit",
        review_status="ACCEPTED",
        source_title="Freelancer bookkeeping discussion",
        source_url="https://www.reddit.com/r/freelance/",
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


def _selected_channel() -> CustomerChannelView:
    return CustomerChannelView(
        platform=DistributionPlatform.REDDIT,
        label="Reddit",
        mode="RESEARCH_ONLY",
        selected=True,
        publisher_mode=PublisherMode.MANUAL,
        capabilities=[
            CustomerChannelCapabilityView(capability=ChannelCapability.SEARCH, ready=True),
            CustomerChannelCapabilityView(
                capability=ChannelCapability.PUBLISH,
                ready=False,
                blocker="select Client-owned as the Reddit publisher mode",
            ),
            CustomerChannelCapabilityView(
                capability=ChannelCapability.MEASURE,
                ready=False,
                blocker="channel outcome adapter is not implemented yet",
            ),
        ],
        autonomous_execution_available=False,
        execution_ready=False,
        connected=False,
    )


def test_starting_move_setup_requires_customer_session() -> None:
    response = TestClient(app).get(
        f"/customer/workspace/{uuid4()}/starting-move/setup"
    )

    assert response.status_code == 401


def test_starting_move_setup_route_is_read_only_customer_scoped(monkeypatch) -> None:
    monkeypatch.setattr(
        route_module.customer_account_service,
        "project_access",
        lambda **_kwargs: (object(), "customer-token"),
    )
    monkeypatch.setattr(
        route_module.customer_funnel_service,
        "get_project_payload",
        lambda project_id, customer_token: {
            "id": str(project_id),
            "customer_token": customer_token,
        },
    )
    monkeypatch.setattr(
        route_module.customer_starting_move_draft_service,
        "view",
        lambda _project: _accepted_draft(),
    )
    monkeypatch.setattr(
        route_module.customer_channel_service,
        "list",
        lambda _project_id, _customer_token: [_selected_channel()],
    )

    response = TestClient(app).get(
        f"/customer/workspace/{PROJECT_ID}/starting-move/setup"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "READY_FOR_HANDOFF"
    assert payload["platform"] == "REDDIT"
    assert payload["execution_allowed"] is False
    assert payload["review_status"] == "ACCEPTED"
