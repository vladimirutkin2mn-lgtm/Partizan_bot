from __future__ import annotations

import argparse
import json
from uuid import UUID

from app.customer_channels import customer_channel_service
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_execution_service import distribution_execution_service
from app.runtime_store import get_runtime_store
from app.telegram_client_governance import customer_telegram_governance_service
from app.telegram_client_publishing import customer_telegram_client_publish_service
from app.customer_telegram_rollout import ROLLOUT_NAMESPACE, _marker_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only diagnostic for a failed first Telegram rollout."
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--telegram-entity-id", type=int, required=True)
    return parser


def run(args: argparse.Namespace) -> dict:
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(args.project_id))
    if project is None:
        raise ValueError("Customer project not found")

    marker = store.get(
        ROLLOUT_NAMESPACE,
        _marker_key(args.project_id, args.telegram_entity_id),
    )
    if marker is None:
        raise ValueError("Telegram rollout marker not found")

    action_id_raw = marker.get("action_id")
    action = None
    receipt = None
    if action_id_raw:
        action_id = UUID(str(action_id_raw))
        action = distribution_execution_service.get_action(action_id)
        receipt = customer_telegram_client_publish_service.get_receipt(action_id)

    automation = customer_telegram_governance_service.automation_status_internal(
        args.project_id
    )
    channels = customer_channel_service.autonomous_platforms(project)

    return {
        "project_id": str(args.project_id),
        "project_research_state": project.get("research_state"),
        "telegram_mode": (
            (project.get("channel_preferences") or {}).get("TELEGRAM")
            if isinstance(project.get("channel_preferences"), dict)
            else None
        ),
        "telegram_publisher_mode": (
            (project.get("channel_publisher_modes") or {}).get("TELEGRAM")
            if isinstance(project.get("channel_publisher_modes"), dict)
            else None
        ),
        "autonomous_platforms": [item.value for item in channels],
        "automation": automation.model_dump(mode="json"),
        "rollout_marker": marker,
        "action": (
            {
                "id": str(action.id),
                "status": action.status.value,
                "platform": action.platform.value,
                "action_type": action.action_type.value,
                "target_url": str(action.target_url or ""),
                "content_text": str(action.content_text or ""),
                "context_text": str(action.content_payload.get("context_text") or ""),
                "operational_metadata": action.operational_metadata,
            }
            if action is not None
            else None
        ),
        "telegram_receipt": (
            receipt.model_dump(mode="json") if receipt is not None else None
        ),
    }


def main() -> int:
    args = build_parser().parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {"error_type": type(exc).__name__, "error": str(exc)[:2000]},
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
