from pathlib import Path

from app import growth_run
from app.growth_run_cli import AuthenticatedApiClient, _bounded_build_parser


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


def test_authenticated_runner_bounds_distribution_discovery(monkeypatch) -> None:
    calls: list[tuple[str, str, dict | None, bool]] = []

    def fake_request(
        self,
        method: str,
        path: str,
        *,
        body=None,
        query=None,
        operator: bool = False,
    ):
        del self, body
        calls.append((method, path, query, operator))
        return {"ok": True}

    monkeypatch.setattr(growth_run.ApiClient, "request", fake_request)
    client = AuthenticatedApiClient(
        "https://partizan.example.com",
        operator_key="runtime-secret",
    )
    client.top_icp_count = 1

    client.post("/v1/products/product-1/distribution/discover")

    assert calls == [
        (
            "POST",
            "/v1/products/product-1/distribution/discover",
            {"top_icp_count": 1},
            True,
        )
    ]


def test_bounded_parser_preserves_default_and_allows_one_icp() -> None:
    parser = _bounded_build_parser()

    default_args = parser.parse_args(["--product-id", "product-1"])
    bounded_args = parser.parse_args(
        ["--product-id", "product-1", "--top-icp-count", "1"]
    )

    assert default_args.top_icp_count == 3
    assert bounded_args.top_icp_count == 1


def test_growth_run_console_script_uses_authenticated_entrypoint() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'partizan-growth-run = "app.growth_run_cli:main"' in pyproject
    assert "--operator-key" not in pyproject
