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
}

RETIRED_ROUTE_FRAGMENTS = {
    "/channels/discover",
    "/growth-plays/generate",
    "/execution-packages/",
    "/mock-workflow",
}


def test_retired_runtime_files_stay_absent() -> None:
    root = Path(__file__).resolve().parents[1]

    present = sorted(path for path in RETIRED_RUNTIME_FILES if (root / path).exists())

    assert present == []


def test_main_does_not_mount_retired_runtime_routes() -> None:
    route_paths = {route.path for route in app.routes}

    for fragment in RETIRED_ROUTE_FRAGMENTS:
        assert all(fragment not in path for path in route_paths)


def test_retired_http_paths_are_not_served_locally() -> None:
    assert client.post("/v1/products/00000000-0000-0000-0000-000000000000/channels/discover").status_code == 404
    assert client.post("/v1/products/00000000-0000-0000-0000-000000000000/growth-plays/generate").status_code == 404
    assert client.post("/v1/execution-packages/00000000-0000-0000-0000-000000000000/run").status_code == 404
    assert client.post("/v1/products/00000000-0000-0000-0000-000000000000/mock-workflow").status_code == 404
