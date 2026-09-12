import pytest
from fastapi.testclient import TestClient

import app.customer_channel_routes as customer_channel_routes_module
from app.customer_account import customer_account_service
from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerPreviewRequest
from app.growth_balance import growth_balance_service
from app.main import app
from app.runtime_store import get_runtime_store


@pytest.fixture(autouse=True)
def reset_customer_channel_selection_state():
    previous_meta_public_ready = customer_channel_service._settings.meta_oauth_public_ready
    customer_channel_service._settings.meta_oauth_public_ready = True
    customer_account_service.reset()
    customer_funnel_service.reset()
    growth_balance_service.reset()
    try:
        yield
    finally:
        customer_channel_service._settings.meta_oauth_public_ready = previous_meta_public_ready


def _registered_client() -> tuple[TestClient, object]:
    client = TestClient(app)
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    response = client.post(
        "/customer/account/register",
        json={
            "email": "channel-choice@example.com",
            "password": "correct-horse-42",
            "project_id": str(preview.project_id),
            "customer_token": preview.customer_token,
        },
    )
    assert response.status_code == 200
    return client, preview


def _stored_project(preview: object) -> dict:
    project_id = getattr(preview, "project_id")
    project = get_runtime_store().get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
    assert project is not None
    return project


def _save_project(preview: object, project: dict) -> None:
    project_id = getattr(preview, "project_id")
    get_runtime_store().put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)


def _reddit_preview_opportunity(*, url: str = "https://www.reddit.com/r/freelance/") -> dict:
    return {
        "surface": "COMMUNITY",
        "title": "Freelancer bookkeeping discussion",
        "url": url,
        "rationale": "Freelancers are already discussing bookkeeping workflow pain here.",
        "relevance_score": 91,
        "execution_status": "MANUAL_HANDOFF",
        "execution_requirement": "Review the community context before any contribution.",
        "provenance": [
            {
                "query": "freelancer bookkeeping reddit",
                "title": "Freelancer bookkeeping thread",
                "url": url,
                "snippet": "Freelancers compare bookkeeping workflows and tools.",
            }
        ],
        "recommended_action": "Review the thread and prepare one useful bookkeeping contribution.",
        "estimated_cost_min_usd": 0,
        "estimated_cost_max_usd": 0,
        "signal_to_watch": "Qualified replies and product visits.",
    }


def test_channel_selection_requires_customer_session() -> None:
    preview = customer_funnel_service.create_preview(
        CustomerPreviewRequest(
            brief="AI bookkeeping assistant for US freelancers with a monthly subscription.",
            website_url="https://example.com",
            market="United States",
            goal="Get paying customers",
            budget_usd=1000,
        )
    )
    client = TestClient(app)

    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    starting_move = client.get(
        f"/customer/workspace/{preview.project_id}/starting-move",
    )

    assert selected.status_code == 401
    assert starting_move.status_code == 401


def test_selecting_starting_channel_does_not_enable_execution_or_refresh_policy(monkeypatch) -> None:
    client, preview = _registered_client()

    def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("starting-channel intent must not rebuild execution policy")

    monkeypatch.setattr(
        customer_channel_routes_module.customer_autopilot_service,
        "refresh_channel_policy",
        unexpected_refresh,
    )

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )

    assert response.status_code == 200
    channels = {item["platform"]: item for item in response.json()}
    assert channels["REDDIT"]["selected"] is True
    assert sum(bool(item["selected"]) for item in channels.values()) == 1
    assert all(item["mode"] == "RESEARCH_ONLY" for item in channels.values())
    assert all(item["publisher_mode"] == "MANUAL" for item in channels.values())

    project = _stored_project(preview)
    assert project["selected_acquisition_channel"] == "REDDIT"
    assert project.get("channel_preferences") is None
    assert project.get("channel_publisher_modes") is None
    assert customer_channel_service.autonomous_platforms(project) == []


def test_matching_preview_evidence_becomes_the_selected_channel_starting_move() -> None:
    client, preview = _registered_client()
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    assert selected.status_code == 200
    project = _stored_project(preview)
    project["preview"]["free_opportunity"] = _reddit_preview_opportunity()
    _save_project(preview, project)

    response = client.get(f"/customer/workspace/{preview.project_id}/starting-move")

    assert response.status_code == 200
    move = response.json()
    assert move["platform"] == "REDDIT"
    assert move["state"] == "READY"
    assert move["source"] == "PREVIEW_RESEARCH"
    assert move["title"] == "Freelancer bookkeeping discussion"
    assert move["recommended_action"] == (
        "Review the thread and prepare one useful bookkeeping contribution."
    )
    assert move["signal_to_watch"] == "Qualified replies and product visits."
    assert move["url"].startswith("https://www.reddit.com/")


def test_full_research_exact_channel_move_wins_over_preview_evidence() -> None:
    client, preview = _registered_client()
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    assert selected.status_code == 200
    project = _stored_project(preview)
    project["preview"]["free_opportunity"] = _reddit_preview_opportunity()
    project["research"] = {
        "icps": [],
        "opportunities": [
            {
                "platform": "REDDIT",
                "kind": "COMMUNITY",
                "title": "Exact Reddit acquisition thread",
                "url": "https://www.reddit.com/r/freelance/comments/example/",
                "rationale": "Full research found a higher-confidence Reddit opportunity.",
                "relevance_score": 96,
                "surface": "EXECUTION_PLATFORM",
                "execution_status": "PARTIZAN_CONTROL_PLANE",
                "execution_requirement": (
                    "Execution still requires an enabled channel and explicit permission."
                ),
                "provenance": [
                    {
                        "query": "reddit bookkeeping workflow",
                        "title": "Exact Reddit acquisition thread",
                        "url": "https://www.reddit.com/r/freelance/comments/example/",
                        "snippet": "A concrete discussion from full research.",
                    }
                ],
            }
        ],
    }
    _save_project(preview, project)

    response = client.get(f"/customer/workspace/{preview.project_id}/starting-move")

    assert response.status_code == 200
    move = response.json()
    assert move["state"] == "READY"
    assert move["source"] == "FULL_RESEARCH"
    assert move["title"] == "Exact Reddit acquisition thread"
    assert "Do not publish or spend" in move["recommended_action"]


def test_selected_channel_without_matching_evidence_returns_research_gap() -> None:
    client, preview = _registered_client()
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "TELEGRAM"},
    )
    assert selected.status_code == 200
    project = _stored_project(preview)
    project["preview"]["free_opportunity"] = _reddit_preview_opportunity()
    _save_project(preview, project)

    response = client.get(f"/customer/workspace/{preview.project_id}/starting-move")

    assert response.status_code == 200
    move = response.json()
    assert move["platform"] == "TELEGRAM"
    assert move["state"] == "NEEDS_RESEARCH"
    assert move["source"] == "SELECTED_CHANNEL"
    assert move["url"] is None
    assert "does not yet have" in move["rationale"]
    assert "Do not connect an account or fund a test" in move["recommended_action"]
    assert "No execution permission" in move["execution_requirement"]


def test_lookalike_hostname_is_not_treated_as_channel_evidence() -> None:
    client, preview = _registered_client()
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    assert selected.status_code == 200
    project = _stored_project(preview)
    project["preview"]["free_opportunity"] = _reddit_preview_opportunity(
        url="https://reddit.com.example.test/fake",
    )
    _save_project(preview, project)

    response = client.get(f"/customer/workspace/{preview.project_id}/starting-move")

    assert response.status_code == 200
    assert response.json()["state"] == "NEEDS_RESEARCH"


def test_off_channel_cannot_be_selected() -> None:
    client, preview = _registered_client()
    turned_off = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "TELEGRAM", "mode": "OFF"}]},
    )
    assert turned_off.status_code == 200

    response = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "TELEGRAM"},
    )

    assert response.status_code == 409
    assert "turned off" in response.json()["detail"]
    current = client.get(f"/customer/workspace/{preview.project_id}/channels")
    assert current.status_code == 200
    assert not any(item["selected"] for item in current.json())


def test_turning_selected_channel_off_clears_only_the_selection_intent() -> None:
    client, preview = _registered_client()
    selected = client.put(
        f"/customer/workspace/{preview.project_id}/channel-selection",
        json={"platform": "REDDIT"},
    )
    assert selected.status_code == 200

    turned_off = client.put(
        f"/customer/workspace/{preview.project_id}/channels",
        json={"channels": [{"platform": "REDDIT", "mode": "OFF"}]},
    )

    assert turned_off.status_code == 200
    channels = {item["platform"]: item for item in turned_off.json()}
    assert channels["REDDIT"]["mode"] == "OFF"
    assert channels["REDDIT"]["selected"] is False
    assert not any(item["selected"] for item in channels.values())

    project = _stored_project(preview)
    assert "selected_acquisition_channel" not in project
    assert "acquisition_channel_selected_at" not in project
    assert project["channel_preferences"]["REDDIT"] == "OFF"
    assert customer_channel_service.autonomous_platforms(project) == []
