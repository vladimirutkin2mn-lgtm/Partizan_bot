from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.config import get_settings
from app.distribution_analytics_service import (
    DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
    distribution_analytics_service,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_growth_manager_service import distribution_growth_manager_service
from app.distribution_play_service import distribution_play_service
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.tracking_routes import TRACKING_VISITOR_COOKIE

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARTIZAN_PUBLIC_BASE_URL", raising=False)
    get_settings.cache_clear()
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    growth_play_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_analytics_service.reset()
    distribution_growth_manager_service.reset()
    client.cookies.clear()
    yield
    monkeypatch.delenv("PARTIZAN_PUBLIC_BASE_URL", raising=False)
    get_settings.cache_clear()
    client.cookies.clear()


def _product() -> str:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle Tracking\n"
                "Description: AI entertainment product with personalized insights.\n"
                "Problem: Users want fast personalized guidance.\n"
                "Value proposition: Personalized answers available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                "Budget: 500\n"
                "Max CAC: 10\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution-plays/generate").status_code == 200
    return product_id


def _prepare(product_id: str) -> dict:
    plays = client.get(f"/v1/products/{product_id}/distribution-plays")
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == "instagram_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://product.example/start?existing=1"},
    )
    assert prepared.status_code == 200
    return prepared.json()


def _run(plan: dict) -> dict:
    action_id = plan["action"]["id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    running = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        json={"external_reference": "tracking-test"},
    )
    assert running.status_code == 200
    return running.json()


def _enable_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARTIZAN_PUBLIC_BASE_URL", "https://partizan.example")
    get_settings.cache_clear()


def test_direct_tracking_url_is_unchanged_when_public_base_is_not_configured() -> None:
    product_id = _product()
    plan = _prepare(product_id)

    tracking_url = plan["experiment"]["tracking_url"]
    parts = urlsplit(tracking_url)
    query = parse_qs(parts.query)

    assert parts.scheme == "https"
    assert parts.netloc == "product.example"
    assert parts.path == "/start"
    assert query["existing"] == ["1"]
    assert query["utm_source"] == ["partizan"]
    assert query["ptz_experiment"] == [plan["experiment"]["id"]]
    assert query["ptz_action"] == [plan["action"]["id"]]


def test_configured_public_base_generates_redirect_tracking_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_redirect(monkeypatch)
    product_id = _product()
    plan = _prepare(product_id)

    experiment = plan["experiment"]
    assert experiment["tracking_url"] == (
        f"https://partizan.example/r/{experiment['referral_token']}"
    )
    assert plan["action"]["tracking_url"] == experiment["tracking_url"]


def test_running_redirect_records_visit_and_preserves_destination_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_redirect(monkeypatch)
    product_id = _product()
    running = _run(_prepare(product_id))
    experiment = running["experiment"]

    response = client.get(
        f"/r/{experiment['referral_token']}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    location = response.headers["location"]
    parts = urlsplit(location)
    query = parse_qs(parts.query)
    assert parts.netloc == "product.example"
    assert query["existing"] == ["1"]
    assert query["utm_source"] == ["partizan"]
    assert query["ptz_experiment"] == [experiment["id"]]
    assert query["ptz_action"] == [running["action"]["id"]]
    assert TRACKING_VISITOR_COOKIE in response.headers["set-cookie"]
    assert "HttpOnly" in response.headers["set-cookie"]

    analytics = client.get(
        f"/v1/distribution-experiments/{experiment['id']}/analytics"
    )
    assert analytics.status_code == 200
    assert analytics.json()["event_count"] == 1
    assert analytics.json()["metrics"]["visits"] == 1


def test_repeat_click_reuses_visitor_cookie_but_counts_two_visits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_redirect(monkeypatch)
    product_id = _product()
    running = _run(_prepare(product_id))
    experiment = running["experiment"]
    path = f"/r/{experiment['referral_token']}"

    first = client.get(path, follow_redirects=False)
    assert first.status_code == 302
    second = client.get(path, follow_redirects=False)
    assert second.status_code == 302
    assert TRACKING_VISITOR_COOKIE not in second.headers.get("set-cookie", "")

    analytics = client.get(
        f"/v1/distribution-experiments/{experiment['id']}/analytics"
    ).json()
    assert analytics["metrics"]["visits"] == 2

    stored_events = get_runtime_store().list_namespace(DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE)
    visit_actors = {
        event["actor_id"]
        for event in stored_events
        if event["experiment_id"] == experiment["id"] and event["event_type"] == "VISIT"
    }
    assert len(visit_actors) == 1
    assert next(iter(visit_actors)).startswith("visitor:")


def test_draft_redirect_still_redirects_but_does_not_record_visit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_redirect(monkeypatch)
    product_id = _product()
    plan = _prepare(product_id)
    experiment = plan["experiment"]

    response = client.get(
        f"/r/{experiment['referral_token']}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    analytics = client.get(
        f"/v1/distribution-experiments/{experiment['id']}/analytics"
    )
    assert analytics.status_code == 200
    assert analytics.json()["event_count"] == 0


def test_unknown_referral_token_returns_404_instead_of_redirecting() -> None:
    response = client.get("/r/not-a-real-token", follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers


def test_request_query_cannot_override_persisted_redirect_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_redirect(monkeypatch)
    product_id = _product()
    running = _run(_prepare(product_id))
    experiment = running["experiment"]

    response = client.get(
        f"/r/{experiment['referral_token']}?url=https://evil.example/phish",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert urlsplit(response.headers["location"]).netloc == "product.example"
    assert "evil.example" not in response.headers["location"]


def test_analytics_failure_never_blocks_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_redirect(monkeypatch)
    product_id = _product()
    running = _run(_prepare(product_id))
    experiment = running["experiment"]

    def fail_ingest(*args, **kwargs):
        raise RuntimeError("analytics unavailable")

    monkeypatch.setattr(distribution_analytics_service, "ingest_event", fail_ingest)
    response = client.get(
        f"/r/{experiment['referral_token']}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert urlsplit(response.headers["location"]).netloc == "product.example"


def test_public_base_url_rejects_path_query_and_non_http_origins() -> None:
    from app.config import Settings

    for invalid in (
        "ftp://partizan.example",
        "https://partizan.example/base",
        "https://partizan.example?x=1",
        "partizan.example",
    ):
        with pytest.raises(ValueError):
            Settings(_env_file=None, partizan_public_base_url=invalid)
