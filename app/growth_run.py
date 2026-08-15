from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_ENRICH_COUNT = 20


class GrowthRunHttpError(RuntimeError):
    def __init__(self, status: int, detail: str, *, path: str) -> None:
        super().__init__(f"HTTP {status} {path}: {detail}")
        self.status = status
        self.detail = detail
        self.path = path


class GrowthRunNetworkError(RuntimeError):
    pass


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        operator_key: str | None = None,
        timeout: float = 90,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.operator_key = operator_key or None
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        operator: bool = False,
    ) -> Any:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if operator and self.operator_key:
            headers["X-Partizan-Operator-Key"] = self.operator_key
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                payload = response.read().decode("utf-8")
                if not payload:
                    return None
                content_type = response.headers.get("Content-Type", "")
                return json.loads(payload) if "json" in content_type else payload
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            detail = raw
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "detail" in parsed:
                    detail = str(parsed["detail"])
            except json.JSONDecodeError:
                pass
            raise GrowthRunHttpError(exc.code, detail, path=path) from exc
        except (URLError, TimeoutError) as exc:
            raise GrowthRunNetworkError(
                f"Cannot reach Partizan API at {self.base_url}: {exc}"
            ) from exc

    def get(
        self,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        operator: bool = False,
    ) -> Any:
        return self.request("GET", path, query=query, operator=operator)

    def post(
        self,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        operator: bool = False,
    ) -> Any:
        return self.request("POST", path, body=body, query=query, operator=operator)


@dataclass
class GrowthRunReport:
    product_id: str | None = None
    experiment_id: str | None = None
    action_id: str | None = None
    tracking_url: str | None = None
    referral_token: str | None = None
    selected_play: dict[str, Any] | None = None
    blockers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def parse_answer(value: str) -> tuple[str, str]:
    field_name, separator, answer = value.partition("=")
    field_name = field_name.strip().lower()
    answer = answer.strip()
    if not separator or not field_name or not answer:
        raise argparse.ArgumentTypeError("answer must use FIELD=VALUE with both sides non-empty")
    return field_name, answer


def clarification_answers(values: list[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for field_name, answer in values:
        if field_name in result:
            raise ValueError(f"duplicate clarification answer for `{field_name}`")
        result[field_name] = answer
    return result


def read_brief(args: argparse.Namespace) -> str | None:
    if args.brief is not None:
        brief = args.brief.strip()
        return brief or None
    if args.brief_file is None:
        return None
    brief = Path(args.brief_file).read_text(encoding="utf-8").strip()
    return brief or None


def select_play(
    plays: list[dict[str, Any]],
    *,
    platform: str | None = None,
    tactic_class: str | None = None,
) -> dict[str, Any] | None:
    candidates = [play for play in plays if play.get("status") == "READY"]
    if platform:
        candidates = [
            play
            for play in candidates
            if str(play.get("platform", "")).upper() == platform.upper()
        ]
    if tactic_class:
        candidates = [
            play
            for play in candidates
            if str(play.get("tactic_class", "")).upper() == tactic_class.upper()
        ]
    if not candidates:
        return None
    return max(candidates, key=lambda play: float(play.get("priority_score") or 0))


def summarize_blocked_plays(
    plays: list[dict[str, Any]],
    *,
    limit: int = 8,
) -> list[str]:
    summaries: list[str] = []
    seen: set[str] = set()
    ordered = sorted(
        plays,
        key=lambda play: float(play.get("priority_score") or 0),
        reverse=True,
    )
    for play in ordered:
        if play.get("status") != "BLOCKED":
            continue
        blockers = [str(item) for item in play.get("blockers") or []]
        if not blockers:
            continue
        label = f"{play.get('platform')} / {play.get('tactic_class')}: {'; '.join(blockers)}"
        if label in seen:
            continue
        seen.add(label)
        summaries.append(label)
        if len(summaries) >= limit:
            break
    return summaries


def _print_step(label: str, payload: str | None = None) -> None:
    suffix = f": {payload}" if payload else ""
    print(f"[growth-run] {label}{suffix}")


def _create_and_confirm_product(
    client: ApiClient,
    *,
    brief: str,
    reference_links: list[str],
    answers: dict[str, str],
    report: GrowthRunReport,
) -> dict[str, Any]:
    response = client.post(
        "/v1/products",
        body={"brief": brief, "reference_links": reference_links},
    )
    product = response["product"]
    report.product_id = str(product["id"])
    _print_step("product created", report.product_id)

    clarifications = list(response.get("clarifications") or [])
    while clarifications:
        question = clarifications[0]
        field_name = str(question.get("field_name") or "").strip().lower()
        answer = answers.get(field_name)
        if answer is None:
            report.blockers.append(
                "Missing explicit clarification answer. "
                f"Re-run with --answer '{field_name}=<value>' for: {question.get('question')}"
            )
            return product
        _print_step("answer clarification", field_name)
        response = client.post(
            f"/v1/products/{report.product_id}/clarifications",
            body={"question_id": question["id"], "answer": answer},
        )
        product = response["product"]
        clarifications = list(response.get("clarifications") or [])

    confirmed = client.post(f"/v1/products/{report.product_id}/confirm")
    product = confirmed["product"]
    _print_step("product confirmed", str(product.get("status")))
    return product


def _use_existing_product(
    client: ApiClient,
    product_id: str,
    report: GrowthRunReport,
) -> dict[str, Any]:
    product = client.get(f"/v1/products/{product_id}")
    report.product_id = str(product["id"])
    if str(product.get("status")) != "CONFIRMED":
        report.blockers.append(
            f"Existing ProductProfile must be CONFIRMED; current status={product.get('status')}"
        )
    else:
        _print_step("using confirmed product", report.product_id)
    return product


def _build_distribution(
    client: ApiClient,
    product_id: str,
    *,
    enrich_count: int,
) -> dict[str, Any]:
    icps = client.post(f"/v1/products/{product_id}/icps/generate")
    _print_step(
        "ICPs generated",
        str(icps.get("ranked_count") or len(icps.get("icps") or [])),
    )
    distribution = client.post(f"/v1/products/{product_id}/distribution/discover")
    _print_step(
        "distribution discovered",
        str(len(distribution.get("opportunities") or [])),
    )
    try:
        enriched = client.post(
            f"/v1/products/{product_id}/distribution/enrich",
            query={"max_opportunities": enrich_count},
        )
        _print_step(
            "opportunities enriched",
            str(enriched.get("enriched_count") or enrich_count),
        )
    except GrowthRunHttpError as exc:
        _print_step("enrichment degraded", exc.detail)
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    _print_step(
        "distribution plays generated",
        f"ready={plays.get('ready_count', 0)} blocked={plays.get('blocked_count', 0)}",
    )
    return plays


def _prepare_selected_play(
    client: ApiClient,
    report: GrowthRunReport,
    *,
    destination_url: str | None,
) -> None:
    assert report.product_id is not None
    assert report.selected_play is not None
    if not destination_url:
        report.blockers.append(
            "No destination URL. Pass --destination-url or --reference-link before action prepare."
        )
        return
    play_id = str(report.selected_play["id"])
    try:
        plan = client.post(
            f"/v1/products/{report.product_id}/distribution-plays/{play_id}/actions/auto-prepare",
            body={"destination_url": destination_url},
        )
    except GrowthRunHttpError as exc:
        report.blockers.append(f"Action prepare blocked: {exc.detail}")
        return
    action = plan["action"]
    experiment = plan["experiment"]
    report.action_id = str(action["id"])
    report.experiment_id = str(experiment["id"])
    report.tracking_url = (
        str(experiment.get("tracking_url") or action.get("tracking_url") or "") or None
    )
    report.referral_token = str(experiment.get("referral_token") or "") or None
    _print_step("action prepared", report.action_id)


def _execute_if_requested(
    client: ApiClient,
    report: GrowthRunReport,
    *,
    execute: bool,
) -> None:
    if not execute or report.action_id is None:
        return
    try:
        client.post(
            f"/v1/distribution-actions/{report.action_id}/approve",
            operator=True,
        )
        result = client.post(
            f"/v1/distribution-actions/{report.action_id}/execute",
            body={"retry": False},
            operator=True,
        )
    except GrowthRunHttpError as exc:
        report.blockers.append(f"External execution blocked: {exc.detail}")
        return
    receipt = result.get("receipt") or {}
    outcome = str(receipt.get("outcome") or "UNKNOWN")
    _print_step("adapter outcome", outcome)
    if outcome == "STAGED":
        report.notes.append(
            "Paid provider objects are STAGED only; this runner never activates spend."
        )
    elif outcome == "EXECUTED":
        report.notes.append(
            "Adapter confirmed external execution and the experiment is RUNNING."
        )
    elif outcome in {"ASSISTED", "UNAVAILABLE", "FAILED", "IN_PROGRESS"}:
        report.blockers.append(f"Adapter outcome {outcome}: {receipt.get('message')}")


def _inspect_product_loop(client: ApiClient, report: GrowthRunReport) -> None:
    if report.product_id is None:
        return
    try:
        integration = client.get(
            f"/v1/products/{report.product_id}/integration-status",
            operator=True,
        )
        report.notes.append(
            "Integration: "
            f"event_key={integration.get('event_key_configured')}, "
            f"public_tracking={integration.get('public_tracking_configured')}, "
            f"observed={','.join(integration.get('observed_event_types') or []) or '-'}"
        )
    except GrowthRunHttpError as exc:
        report.notes.append(f"Integration status unavailable: {exc.detail}")

    try:
        analytics = client.get(
            f"/v1/products/{report.product_id}/distribution-analytics"
        )
        report.notes.append(
            "Analytics: "
            f"experiments={analytics.get('experiment_count', 0)}, "
            f"spend={analytics.get('total_spend', 0)}, "
            f"paid={analytics.get('total_paid_users', 0)}, "
            f"revenue={analytics.get('total_revenue', 0)}, "
            f"blended_cac={analytics.get('blended_cac')}"
        )
    except GrowthRunHttpError as exc:
        report.notes.append(f"Analytics unavailable: {exc.detail}")

    try:
        learning = client.get(f"/v1/products/{report.product_id}/distribution-learning")
        report.notes.append(
            f"Learning memory entries: {len(learning.get('entries') or [])}"
        )
    except GrowthRunHttpError as exc:
        report.notes.append(f"Learning memory unavailable: {exc.detail}")


def run(args: argparse.Namespace) -> GrowthRunReport:
    operator_key = os.getenv("PARTIZAN_OPERATOR_KEY") or os.getenv("OPERATOR_API_KEY")
    client = ApiClient(args.base_url, operator_key=operator_key, timeout=args.timeout)
    report = GrowthRunReport()

    health = client.get("/health/ready")
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise RuntimeError("Partizan /health/ready did not return status=ok")
    _print_step("API ready", args.base_url)

    if args.product_id:
        _use_existing_product(client, args.product_id, report)
    else:
        brief = read_brief(args)
        if brief is None:
            report.blockers.append(
                "Provide --product-id or exactly one of --brief / --brief-file."
            )
            return report
        answers = clarification_answers(args.answer)
        _create_and_confirm_product(
            client,
            brief=brief,
            reference_links=args.reference_link,
            answers=answers,
            report=report,
        )
    if report.blockers or report.product_id is None:
        return report

    plays_result = _build_distribution(
        client,
        report.product_id,
        enrich_count=args.enrich_count,
    )
    plays = list(plays_result.get("plays") or [])
    selected = select_play(
        plays,
        platform=args.platform,
        tactic_class=args.tactic_class,
    )
    if selected is None:
        report.blockers.extend(summarize_blocked_plays(plays))
        if not report.blockers:
            report.blockers.append(
                "No READY DistributionPlay matched the requested filters."
            )
        return report

    report.selected_play = selected
    _print_step(
        "selected play",
        (
            f"{selected.get('platform')} / {selected.get('tactic_class')} / "
            f"priority={selected.get('priority_score')}"
        ),
    )

    destination_url = args.destination_url or (
        args.reference_link[0] if args.reference_link else None
    )
    _prepare_selected_play(client, report, destination_url=destination_url)
    _execute_if_requested(client, report, execute=args.execute)
    _inspect_product_loop(client, report)

    if not args.execute and report.action_id:
        report.notes.append(
            "Dry-run complete at PREPARED action. Re-run with --execute for approve + adapter execution."
        )
    if args.execute and not operator_key:
        report.notes.append(
            "No PARTIZAN_OPERATOR_KEY/OPERATOR_API_KEY was supplied; local/dev may allow this, production will not."
        )
    return report


def print_report(report: GrowthRunReport, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str))
        return

    print("\n=== Partizan growth run ===")
    print(f"product_id: {report.product_id or '-'}")
    if report.selected_play:
        print(
            "selected_play: "
            f"{report.selected_play.get('platform')} / "
            f"{report.selected_play.get('tactic_class')} / "
            f"{report.selected_play.get('opportunity_title')}"
        )
    print(f"action_id: {report.action_id or '-'}")
    print(f"experiment_id: {report.experiment_id or '-'}")
    print(f"tracking_url: {report.tracking_url or '-'}")
    print(f"referral_token: {report.referral_token or '-'}")
    if report.blockers:
        print("blockers:")
        for blocker in report.blockers:
            print(f"  - {blocker}")
    else:
        print("blockers: none in the traversed path")
    if report.notes:
        print("notes:")
        for note in report.notes:
            print(f"  - {note}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the generic Partizan product -> distribution -> experiment pipeline"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--product-id", default=None)
    source.add_argument("--brief", default=None)
    source.add_argument("--brief-file", default=None)
    parser.add_argument("--answer", action="append", type=parse_answer, default=[])
    parser.add_argument("--destination-url", default=None)
    parser.add_argument("--reference-link", action="append", default=[])
    parser.add_argument(
        "--platform",
        choices=["TELEGRAM", "INSTAGRAM", "REDDIT", "TIKTOK"],
        default=None,
    )
    parser.add_argument(
        "--tactic-class",
        choices=["COMMUNITY", "PAID_PLATFORM", "OWNED_ORGANIC"],
        default=None,
    )
    parser.add_argument("--enrich-count", type=int, default=DEFAULT_ENRICH_COUNT)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Approve and invoke the existing execution adapter. Paid actions still stop at STAGED.",
    )
    parser.add_argument("--json", action="store_true", help="Print the final report as JSON")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if not 1 <= args.enrich_count <= 50:
        raise SystemExit("enrich-count must be between 1 and 50")
    if args.timeout <= 0:
        raise SystemExit("timeout must be > 0")
    try:
        report = run(args)
    except (GrowthRunNetworkError, GrowthRunHttpError, RuntimeError, ValueError, OSError) as exc:
        print(f"growth run failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print_report(report, as_json=args.json)
    raise SystemExit(1 if report.blockers else 0)


if __name__ == "__main__":
    main()
