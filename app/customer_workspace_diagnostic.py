from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.audience_intelligence_service import audience_intelligence_service
from app.customer_account import (
    CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE,
    customer_account_service,
)
from app.customer_account_schemas import CustomerWorkspaceView
from app.customer_autopilot import customer_autopilot_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE, customer_funnel_service
from app.customer_schemas import CustomerDirectionView, CustomerFreeOpportunityView
from app.runtime_store import get_runtime_store


def _safe_message(exc: Exception) -> str:
    return " ".join(str(exc).split())[:500]


def diagnose(project_id: UUID) -> dict:
    store = get_runtime_store()
    report: dict = {
        "project_id": str(project_id),
        "status": "OK",
        "steps": [],
    }

    def run(name: str, fn):
        try:
            value = fn()
        except Exception as exc:  # operational diagnostic: report the exact server failure
            report["status"] = "FAIL"
            report["steps"].append(
                {
                    "step": name,
                    "status": "FAIL",
                    "exception": type(exc).__name__,
                    "message": _safe_message(exc),
                }
            )
            raise
        report["steps"].append({"step": name, "status": "OK"})
        return value

    project_record = run(
        "runtime_project",
        lambda: store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id)),
    )
    if not project_record:
        report["status"] = "FAIL"
        report["steps"].append(
            {
                "step": "runtime_project",
                "status": "FAIL",
                "exception": "LookupError",
                "message": "Project record not found",
            }
        )
        return report

    account_id = str(project_record.get("customer_account_id") or "")
    if not account_id:
        report["status"] = "FAIL"
        report["steps"].append(
            {
                "step": "project_owner",
                "status": "FAIL",
                "exception": "LookupError",
                "message": "Project has no customer account owner",
            }
        )
        return report

    access = run(
        "project_access_record",
        lambda: store.get(
            CUSTOMER_ACCOUNT_PROJECT_ACCESS_NAMESPACE,
            f"{account_id}:{project_id}",
        ),
    )
    customer_token = str((access or {}).get("customer_token") or "")
    if not customer_token:
        report["status"] = "FAIL"
        report["steps"].append(
            {
                "step": "project_access_record",
                "status": "FAIL",
                "exception": "LookupError",
                "message": "Project access token is missing",
            }
        )
        return report

    try:
        account = run(
            "account_view",
            lambda: customer_account_service.view(UUID(account_id)),
        )
        project_payload = run(
            "project_payload",
            lambda: customer_funnel_service.get_project_payload(project_id, customer_token),
        )
        project = run(
            "project_view",
            lambda: customer_funnel_service.get_project(project_id, customer_token),
        )
        clarifications = run(
            "research_clarifications",
            lambda: customer_funnel_service.current_research_clarifications(
                project_id,
                customer_token,
            ),
        )

        product_id_raw = project_payload.get("product_id")
        research_diagnostics: dict = {}
        if product_id_raw:
            try:
                audience_map = audience_intelligence_service.get(UUID(str(product_id_raw)))
            except (KeyError, TypeError, ValueError):
                report["steps"].append(
                    {"step": "audience_intelligence", "status": "MISSING_ALLOWED"}
                )
            else:
                research_diagnostics = dict(audience_map.diagnostics)
                report["steps"].append(
                    {"step": "audience_intelligence", "status": "OK"}
                )

        autopilot = run(
            "autopilot_overview",
            lambda: customer_autopilot_service.overview(project_id, customer_token),
        )

        preview_payload = project_payload.get("preview") or {}
        preview_directions = run(
            "preview_directions",
            lambda: [
                CustomerDirectionView.model_validate(item)
                for item in preview_payload.get("directions", [])
                if isinstance(item, dict)
            ],
        )
        preview_opportunity_payload = preview_payload.get("free_opportunity")
        preview_opportunity = run(
            "preview_opportunity",
            lambda: (
                CustomerFreeOpportunityView.model_validate(preview_opportunity_payload)
                if isinstance(preview_opportunity_payload, dict)
                else None
            ),
        )

        target_max_cac_raw = project_payload.get("autopilot_target_max_cac")
        run(
            "workspace_schema",
            lambda: CustomerWorkspaceView(
                account=account,
                project=project,
                autopilot=autopilot,
                preview_directions=preview_directions,
                preview_research_status=str(
                    preview_payload.get("free_research_status") or "NOT_RUN"
                ),
                preview_research_message=str(
                    preview_payload.get("free_research_message") or ""
                ),
                preview_opportunity=preview_opportunity,
                research_clarifications=clarifications,
                research_diagnostics=research_diagnostics,
                target_max_cac=(
                    float(target_max_cac_raw)
                    if target_max_cac_raw is not None
                    else None
                ),
                autonomous_spend_confirmed=bool(
                    project_payload.get("autopilot_spend_confirmed")
                ),
            ),
        )
    except Exception:
        return report

    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", required=True)
    args = parser.parse_args()
    report = diagnose(UUID(args.project_id))
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
