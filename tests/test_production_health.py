from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

client = TestClient(app)


def test_liveness_is_dependency_free() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_database() -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "available"}


def test_version_exposes_exact_runtime_release_sha(monkeypatch) -> None:
    release_sha = "a" * 40
    monkeypatch.setenv("PARTIZAN_RELEASE_SHA", release_sha)
    get_settings.cache_clear()
    try:
        response = client.get("/version")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json() == {
        "service": "partizan",
        "api_version": "0.8.0",
        "release_sha": release_sha,
    }
