from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


RETIRED_RUNTIME_FILES = {
    "app/analytics_routes.py",
    "app/analytics_service.py",
    "app/channel_hunter.py",
    "app/channel_service.py",
    "app/dogfood.py",
    "app/execution.py",
    "app/execution_service.py",
    "app/growth_manager.py",
    "app/growth_manager_routes.py",
    "app/growth_manager_service.py",
    "app/growth_play_agent.py",
    "app/growth_play_service.py",
    "app/jobs.py",
    "app/oracle_dogfood.py",
    "app/workflow.py",
    # Customer acquisition runtime retired when Growth Balance replaced the
    # delegated marketing-budget model. /start is now a clean v2 runtime.
    "app/web/start.v1.html",
    "app/web/start.v1.js",
    "app/web/autopilot-first.v1.js",
    "app/web/autopilot-first.v1.css",
}

RETIRED_ROUTE_FRAGMENTS = {
    "/channels/discover",
    "/growth-plays/generate",
    "/execution-packages/",
    "/mock-workflow",
}

RETIRED_CUSTOMER_BUDGET_TERMS = {
    "marketing_budget_usd",
    "remaining_budget_usd",
    "estimated_managed_fee_usd",
    'id="autopilot-budget"',
    "Meta charges your own payment method",
}


def test_retired_runtime_files_stay_absent() -> None:
    root = Path(__file__).resolve().parents[1]

    present = sorted(path for path in RETIRED_RUNTIME_FILES if (root / path).exists())

    assert present == []


def test_main_does_not_mount_retired_runtime_routes() -> None:
    route_paths = {
        path
        for route in app.routes
        if (path := getattr(route, "path", None)) is not None
    }

    for fragment in RETIRED_ROUTE_FRAGMENTS:
        assert all(fragment not in path for path in route_paths)


def test_retired_http_paths_are_not_served_locally() -> None:
    product_id = "00000000-0000-0000-0000-000000000000"
    retired_paths = (
        f"/v1/products/{product_id}/channels/discover",
        f"/v1/products/{product_id}/growth-plays/generate",
        f"/v1/execution-packages/{product_id}/run",
        f"/v1/products/{product_id}/mock-workflow",
    )

    assert all(client.post(path).status_code == 404 for path in retired_paths)


def test_customer_runtime_does_not_restore_delegated_budget_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    runtime_paths = (
        root / "app/customer_autopilot.py",
        root / "app/customer_schemas.py",
        root / "app/web/start.v2.html",
        root / "app/web/start.v2.js",
    )
    combined = "\n".join(path.read_text(encoding="utf-8") for path in runtime_paths)

    for term in RETIRED_CUSTOMER_BUDGET_TERMS:
        assert term not in combined
