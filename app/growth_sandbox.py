from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import NAMESPACE_URL, uuid5

from app.config import Settings

SANDBOX_MODE = "SANDBOX"
SANDBOX_PRODUCT_NAME = "Partizan Isolated Growth Sandbox"
SANDBOX_DESTINATION = "https://sandbox.invalid/product"
SANDBOX_SPEND = 30.0
SANDBOX_PAID_REVENUE = 30.0
SANDBOX_ACTORS = ("sandbox-user-1", "sandbox-user-2", "sandbox-user-3")
SANDBOX_BRIEF = """Product: Partizan Isolated Growth Sandbox
Description: A deterministic subscription product for Partizan's internal growth proof.
Problem: Users need a focused workflow to complete recurring digital tasks faster.
Value proposition: Personalized guidance that reduces time spent deciding what to do next.
Use cases: Daily workflow guidance; personalized recommendations; progress review.
USP: A focused subscription experience with persistent context rather than generic one-off answers.
Pricing: $30/month subscription.
Monetization: Monthly digital subscription.
Market: US.
Language: English.
Target audience: English-speaking US adults who pay for productivity software.
Business goal: Acquire 30 paid users.
Budget: $300.
Max CAC: $15.
Allowed channels: Instagram paid social, TikTok paid social, owned content, creator outreach.
Restrictions: No deceptive claims, no spam, no external provider mutation in sandbox mode.
"""
SANDBOX_CLARIFICATION_ANSWERS = {
    "name": SANDBOX_PRODUCT_NAME,
    "product_name": SANDBOX_PRODUCT_NAME,
    "description": "Deterministic digital subscription sandbox product.",
    "problem": "Users need a focused workflow for recurring digital tasks.",
    "value_proposition": "Personalized guidance that reduces decision time.",
    "usp": "Focused subscription experience with persistent context.",
    "pricing": "$30/month subscription",
    "price": "$30/month subscription",
    "monetization": "Monthly digital subscription",
    "market": "US",
    "geography": "US",
    "language": "English",
    "target_audience": "English-speaking US adults who pay for productivity software",
    "business_goal": "Acquire 30 paid users",
    "goal": "Acquire 30 paid users",
    "budget": "$300",
    "max_cac": "$15",
}


class SandboxError(RuntimeError):
    pass


class SandboxHttpError(SandboxError):
    def __init__(self, status: int, detail: str, path: str) -> None:
        super().__init__(f"HTTP {status} {path}: {detail}")
        self.status = status
        self.detail = detail
        self.path = path


@dataclass(frozen=True)
class SandboxEconomics:
    visits: int
    signups: int
    activated_users: int
    paid_users: int
    spend: float
    revenue: float
    cac: float | None
    roas: float | None


@dataclass(frozen=True)
class SandboxReport:
    mode: str
    isolated_runtime_storage: str
    external_provider_mutation: bool
    product_id: str
    action_id: str
    experiment_id: str
    selected_platform: str
    selected_tactic: str
    economics: SandboxEconomics
    growth_decision: str
    learning_entries: int
    portfolio_items: int
    portfolio_uses_observed_economics: bool
    child_process_terminated: bool


class SandboxApiClient:
    def __init__(self, base_url: str, *, timeout: float = 15) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        data = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict) and "detail" in payload:
                    detail = str(payload["detail"])
            except json.JSONDecodeError:
                pass
            raise SandboxHttpError(exc.code, detail, path) from exc
        except (URLError, TimeoutError) as exc:
            raise SandboxError(f"Sandbox API unavailable at {self.base_url}: {exc}") from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path)

    def post(
        self,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        return self.request("POST", path, body=body, headers=headers)


def _assert_parent_is_not_production() -> None:
    settings = Settings()
    if settings.app_env.strip().lower() == "production":
        raise SandboxError("Refusing sandbox run while APP_ENV=production")


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _sandbox_environment(repository_root: Path, port: int) -> dict[str, str]:
    return {
        "APP_ENV": "sandbox",
        "APP_LOG_LEVEL": "WARNING",
        "RUNTIME_STORAGE": "memory",
        "LLM_PROVIDER": "mock",
        "SEARCH_PROVIDER": "mock",
        "EXECUTION_PROVIDER": "mock",
        "CREATIVE_PROVIDER": "unavailable",
        "CREATIVE_VIDEO_PROVIDER": "unavailable",
        "OPERATOR_AUTH_REQUIRED": "false",
        "PARTIZAN_PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
        "PYTHONPATH": str(repository_root),
        "PYTHONUNBUFFERED": "1",
    }


def _child_error(process: subprocess.Popen[bytes]) -> str:
    if process.stderr is None:
        return ""
    try:
        return process.stderr.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _wait_for_child(client: SandboxApiClient, process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            detail = _child_error(process)
            suffix = f": {detail}" if detail else ""
            raise SandboxError(
                f"Sandbox API exited early with code {process.returncode}{suffix}"
            )
        try:
            payload = client.get("/health/live")
            if isinstance(payload, dict) and payload.get("status") == "ok":
                return
        except SandboxError:
            pass
        time.sleep(0.1)
    raise SandboxError("Sandbox API did not become live within 20 seconds")


@contextmanager
def _isolated_api() -> Iterator[tuple[SandboxApiClient, subprocess.Popen[bytes]]]:
    port = _reserve_local_port()
    env = _sandbox_environment(_repository_root(), port)
    with tempfile.TemporaryDirectory(prefix="partizan-sandbox-") as temp_dir:
        process = subprocess.Popen(  # noqa: S603
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=temp_dir,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        client = SandboxApiClient(f"http://127.0.0.1:{port}")
        try:
            _wait_for_child(client, process)
            yield client, process
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _create_confirmed_product(client: SandboxApiClient) -> str:
    response = client.post("/v1/products", body={"brief": SANDBOX_BRIEF})
    product_id = str(response["product"]["id"])
    clarifications = list(response.get("clarifications") or [])
    while clarifications:
        question = clarifications[0]
        field_name = str(question.get("field_name") or "").strip().lower()
        answer = SANDBOX_CLARIFICATION_ANSWERS.get(field_name)
        if answer is None:
            raise SandboxError(
                "Sandbox fixture is incomplete for clarification "
                f"`{field_name}`: {question.get('question')}"
            )
        response = client.post(
            f"/v1/products/{product_id}/clarifications",
            body={"question_id": question["id"], "answer": answer},
        )
        clarifications = list(response.get("clarifications") or [])
    confirmed = client.post(f"/v1/products/{product_id}/confirm")
    if confirmed["product"]["status"] != "CONFIRMED":
        raise SandboxError("Sandbox ProductProfile did not reach CONFIRMED")
    return product_id


def _generate_ready_plays(client: SandboxApiClient, product_id: str) -> list[dict[str, Any]]:
    client.post(f"/v1/products/{product_id}/icps/generate")
    client.post(f"/v1/products/{product_id}/distribution/discover")
    result = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    ready = [play for play in result.get("plays") or [] if play.get("status") == "READY"]
    if not ready:
        raise SandboxError("Mock sandbox generated no READY DistributionPlay")
    return ready


def _select_sandbox_play(plays: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for play in plays:
        if play.get("action_type") == "OUTREACH_EMAIL":
            continue
        key = (str(play.get("platform")), str(play.get("tactic_id")))
        groups.setdefault(key, []).append(play)
    preferred = [
        group
        for (platform, tactic), group in groups.items()
        if platform == "INSTAGRAM" and tactic == "instagram_ads" and len(group) >= 2
    ]
    candidates = preferred or [group for group in groups.values() if len(group) >= 2]
    if not candidates:
        raise SandboxError(
            "Sandbox needs two READY plays for one platform+tactic to prove portfolio learning"
        )
    group = max(
        candidates,
        key=lambda items: max(float(item.get("priority_score") or 0) for item in items),
    )
    return max(group, key=lambda item: float(item.get("priority_score") or 0))


def _prepare_running_experiment(
    client: SandboxApiClient,
    product_id: str,
    play: dict[str, Any],
) -> tuple[str, str]:
    plan = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        body={"destination_url": SANDBOX_DESTINATION},
    )
    action_id = str(plan["action"]["id"])
    experiment_id = str(plan["experiment"]["id"])
    client.post(f"/v1/distribution-actions/{action_id}/approve")
    running = client.post(
        f"/v1/distribution-actions/{action_id}/mark-executed",
        body={"external_reference": "sandbox-manual-proof"},
    )
    if running["experiment"]["status"] != "RUNNING":
        raise SandboxError("Sandbox experiment did not reach RUNNING")
    return action_id, experiment_id


def _event_id(actor: str, event_type: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"partizan-sandbox:{actor}:{event_type}"))


def _spend_id(experiment_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"partizan-sandbox:{experiment_id}:spend"))


def _ingest_funnel(
    client: SandboxApiClient,
    product_id: str,
    experiment_id: str,
) -> None:
    created = client.post(f"/v1/products/{product_id}/distribution-event-key")
    headers = {"X-Partizan-Event-Key": str(created["event_key"])}
    for actor in SANDBOX_ACTORS:
        for event_type in ("VISIT", "SIGNUP", "ACTIVATED", "PAID"):
            body: dict[str, Any] = {
                "event_id": _event_id(actor, event_type),
                "event_type": event_type,
                "experiment_id": experiment_id,
                "actor_id": actor,
            }
            if event_type == "PAID":
                body["revenue"] = SANDBOX_PAID_REVENUE
            client.post(
                f"/v1/products/{product_id}/distribution-events",
                body=body,
                headers=headers,
            )


def _record_spend(client: SandboxApiClient, experiment_id: str) -> None:
    client.post(
        f"/v1/distribution-experiments/{experiment_id}/spend",
        body={
            "spend_id": _spend_id(experiment_id),
            "amount": SANDBOX_SPEND,
            "properties": {"mode": SANDBOX_MODE},
        },
    )


def _economics(client: SandboxApiClient, experiment_id: str) -> SandboxEconomics:
    analytics = client.get(f"/v1/distribution-experiments/{experiment_id}/analytics")
    metrics = analytics["metrics"]
    return SandboxEconomics(
        visits=int(metrics["visits"]),
        signups=int(metrics["signups"]),
        activated_users=int(metrics["activated_users"]),
        paid_users=int(metrics["paid_users"]),
        spend=float(metrics["spend"]),
        revenue=float(metrics["revenue"]),
        cac=None if metrics["cac"] is None else float(metrics["cac"]),
        roas=None if metrics["roas"] is None else float(metrics["roas"]),
    )


def _portfolio_uses_observed_economics(portfolio: dict[str, Any]) -> bool:
    for item in portfolio.get("items") or []:
        rationale = " ".join(str(part) for part in item.get("rationale") or [])
        if "observed peer CAC" in rationale or "peer tactic produced" in rationale:
            return True
    return False


def _assert_expected_economics(economics: SandboxEconomics) -> None:
    expected_paid = len(SANDBOX_ACTORS)
    expected_revenue = expected_paid * SANDBOX_PAID_REVENUE
    expected_cac = SANDBOX_SPEND / expected_paid
    expected_roas = expected_revenue / SANDBOX_SPEND
    expected_counts = {
        "visits": expected_paid,
        "signups": expected_paid,
        "activated_users": expected_paid,
        "paid_users": expected_paid,
    }
    for field_name, expected_value in expected_counts.items():
        if getattr(economics, field_name) != expected_value:
            raise SandboxError(f"Unexpected sandbox {field_name}: {economics}")
    if economics.spend != SANDBOX_SPEND:
        raise SandboxError(f"Unexpected sandbox spend: {economics.spend}")
    if economics.revenue != expected_revenue:
        raise SandboxError(f"Unexpected sandbox revenue: {economics.revenue}")
    if economics.cac != expected_cac:
        raise SandboxError(f"Unexpected sandbox CAC: {economics.cac}")
    if economics.roas != expected_roas:
        raise SandboxError(f"Unexpected sandbox ROAS: {economics.roas}")


def run_sandbox() -> SandboxReport:
    _assert_parent_is_not_production()
    process: subprocess.Popen[bytes] | None = None
    report_data: dict[str, Any] | None = None
    with _isolated_api() as (client, child):
        process = child
        product_id = _create_confirmed_product(client)
        selected = _select_sandbox_play(_generate_ready_plays(client, product_id))
        action_id, experiment_id = _prepare_running_experiment(
            client,
            product_id,
            selected,
        )
        _ingest_funnel(client, product_id, experiment_id)
        _record_spend(client, experiment_id)
        economics = _economics(client, experiment_id)
        _assert_expected_economics(economics)

        decision = client.post(
            f"/v1/distribution-experiments/{experiment_id}/growth-decision"
        )
        if decision["action"] != "SCALE":
            raise SandboxError(
                f"Expected deterministic SCALE decision, got {decision['action']}"
            )
        learning = client.get(f"/v1/products/{product_id}/distribution-learning")
        if len(learning.get("entries") or []) != 1:
            raise SandboxError(
                "Sandbox Growth Manager did not persist exactly one learning entry"
            )
        portfolio = client.get(
            f"/v1/products/{product_id}/distribution-portfolio?max_items=6"
        )
        uses_observed = _portfolio_uses_observed_economics(portfolio)
        if not uses_observed:
            raise SandboxError(
                "Next sandbox portfolio did not use observed experiment economics"
            )
        report_data = {
            "product_id": product_id,
            "action_id": action_id,
            "experiment_id": experiment_id,
            "selected_platform": str(selected["platform"]),
            "selected_tactic": str(selected["tactic_id"]),
            "economics": economics,
            "growth_decision": str(decision["action"]),
            "learning_entries": len(learning.get("entries") or []),
            "portfolio_items": len(portfolio.get("items") or []),
            "portfolio_uses_observed_economics": uses_observed,
        }

    if process is None or process.poll() is None:
        raise SandboxError("Sandbox child process was not terminated")
    if report_data is None:
        raise SandboxError("Sandbox report was not produced")
    return SandboxReport(
        mode=SANDBOX_MODE,
        isolated_runtime_storage="memory",
        external_provider_mutation=False,
        child_process_terminated=True,
        **report_data,
    )


def print_report(report: SandboxReport, *, as_json: bool = False) -> None:
    payload = asdict(report)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print("=== PARTIZAN SANDBOX — SYNTHETIC / NOT PRODUCTION DATA ===")
    print(f"mode: {report.mode}")
    print(f"product_id: {report.product_id}")
    print(f"experiment_id: {report.experiment_id}")
    print(f"play: {report.selected_platform} / {report.selected_tactic}")
    print(
        "funnel: "
        f"VISIT={report.economics.visits} "
        f"SIGNUP={report.economics.signups} "
        f"ACTIVATED={report.economics.activated_users} "
        f"PAID={report.economics.paid_users}"
    )
    print(
        "economics: "
        f"spend={report.economics.spend:.2f} "
        f"revenue={report.economics.revenue:.2f} "
        f"CAC={report.economics.cac:.2f} "
        f"ROAS={report.economics.roas:.2f}"
    )
    print(f"growth_decision: {report.growth_decision}")
    print(f"learning_entries: {report.learning_entries}")
    print(f"portfolio_items: {report.portfolio_items}")
    print(f"portfolio_uses_observed_economics: {report.portfolio_uses_observed_economics}")
    print(f"external_provider_mutation: {report.external_provider_mutation}")
    print(f"child_process_terminated: {report.child_process_terminated}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run a synthetic, isolated Partizan end-to-end growth proof. "
            "Refuses APP_ENV=production and never calls external execution providers."
        )
    )
    parser.add_argument("--json", action="store_true", help="Print a machine-readable report")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        report = run_sandbox()
    except (SandboxError, OSError, ValueError) as exc:
        print(f"SANDBOX FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print_report(report, as_json=args.json)


if __name__ == "__main__":
    main()
