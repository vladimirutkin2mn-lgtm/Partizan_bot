from pathlib import Path

from app import growth_run
from app.growth_run_cli import AuthenticatedApiClient


def test_authenticated_runner_marks_every_internal_v1_request_as_operator(monkeypatch) -> None:
    calls: list[tuple[str, str, bool]] = []

    def fake_request(
        self,
        method: str,
        path: str,
        *,
        body=None,
        query=None,
        operator: bool = False,
    ):
        del self, body, query
        calls.append((method, path, operator))
        return {"ok": True}

    monkeypatch.setattr(growth_run.ApiClient, "request", fake_request)
    client = AuthenticatedApiClient(
        "https://partizan.example.com",
        operator_key="runtime-secret",
    )

    client.get("/v1/products/product-1")
    client.post("/v1/products", body={"brief": "test product"})
    client.get("/health/ready")

    assert calls == [
        ("GET", "/v1/products/product-1", True),
        ("POST", "/v1/products", True),
        ("GET", "/health/ready", False),
    ]


def test_growth_run_console_script_uses_authenticated_entrypoint() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'partizan-growth-run = "app.growth_run_cli:main"' in pyproject
    assert "--operator-key" not in pyproject
