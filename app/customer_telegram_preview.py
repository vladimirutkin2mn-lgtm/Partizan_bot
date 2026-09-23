from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from app.action_drafting import distribution_action_drafting_service
from app.autonomous_controlled_growth import AUTONOMOUS_GROWTH_ADVISORY_LOCK_KEY
from app.autonomy_schemas import GrowthMandateStatus
from app.autonomy_service import growth_mandate_service
from app.customer_telegram_rollout import (
    _ensure_customer_identity,
    _generate_exact_play,
    _load_exact_target,
    _validate_draft,
)
from app.database_advisory_lock import postgres_session_advisory_lock
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionStatus
from app.telegram_client_governance import (
    TelegramAutomationStatus,
    customer_telegram_governance_service,
)
from app.telegram_client_publishing import CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE

TELEGRAM_PREVIEW_NAMESPACE = "customer_telegram_publish_preview"
PREVIEW_SCHEMA_VERSION = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare conversion-oriented Telegram comment variants for human review "
            "without publishing them."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--expected-telegram-entity-id", type=int, required=True)
    parser.add_argument("--expected-handle", required=True)
    return parser


def _preview_key(project_id: UUID, entity_id: int) -> str:
    return f"{project_id}:{entity_id}:v{PREVIEW_SCHEMA_VERSION}"


def _content_hash(
    target_url: str,
    conversion_mechanism: str,
    variant_name: str,
    content_text: str,
) -> str:
    material = (
        f"{target_url}\n{conversion_mechanism}\n{variant_name}\n{content_text}"
    ).encode()
    return hashlib.sha256(material).hexdigest()


def _render_preview(record: dict) -> dict:
    rendered_variants: list[dict] = []
    for item in record.get("variants", []):
        action = distribution_execution_service.get_action(UUID(str(item["action_id"])))
        rendered_variants.append(
            {
                "conversion_mechanism": str(item["conversion_mechanism"]),
                "variant_name": str(item["variant_name"]),
                "objective": str(item["objective"]),
                "expected_user_next_step": str(item["expected_user_next_step"]),
                "action_id": str(item["action_id"]),
                "action_status": action.status.value,
                "target_url": str(action.target_url or ""),
                "context_text": str(action.content_payload.get("context_text") or ""),
                "content_text": str(action.content_text or ""),
                "content_sha256": str(item["content_sha256"]),
                "published": False,
            }
        )

    return {
        "schema_version": PREVIEW_SCHEMA_VERSION,
        "status": str(record["status"]),
        "project_id": str(record["project_id"]),
        "product_id": str(record["product_id"]),
        "opportunity_id": str(record["opportunity_id"]),
        "play_id": str(record["play_id"]),
        "target_url": str(record["target_url"]),
        "variant_count": len(rendered_variants),
        "variants": rendered_variants,
        "prepared_at": str(record["prepared_at"]),
        "published": False,
    }


def _validate_existing_preview(record: dict) -> None:
    if int(record.get("schema_version") or 0) != PREVIEW_SCHEMA_VERSION:
        raise ValueError("Existing preview uses a stale schema version")
    variants = record.get("variants")
    if not isinstance(variants, list) or not variants:
        raise ValueError("Existing preview has no prepared variants")

    for item in variants:
        action = distribution_execution_service.get_action(UUID(str(item["action_id"])))
        if action.status != DistributionActionStatus.PREPARED:
            raise ValueError("Existing preview action is no longer PREPARED")
        expected_hash = _content_hash(
            str(action.target_url or ""),
            str(item["conversion_mechanism"]),
            str(item["variant_name"]),
            str(action.content_text or ""),
        )
        if expected_hash != str(item.get("content_sha256") or ""):
            raise ValueError("Existing preview content hash no longer matches the prepared action")


async def run(args: argparse.Namespace) -> dict:
    store, project, product, distribution, opportunity, target_url = _load_exact_target(args)
    preview_key = _preview_key(args.project_id, args.expected_telegram_entity_id)

    if str((project.get("channel_preferences") or {}).get("TELEGRAM") or "") != "RESEARCH_ONLY":
        raise ValueError("Telegram must remain RESEARCH_ONLY while awaiting human preview approval")

    automation = customer_telegram_governance_service.automation_status_internal(args.project_id)
    if automation.status == TelegramAutomationStatus.ENABLED:
        raise ValueError("Telegram automation must not be ENABLED while awaiting human approval")

    try:
        mandate = growth_mandate_service.get(product.id)
    except KeyError:
        mandate = None
    if mandate is not None and mandate.status == GrowthMandateStatus.ACTIVE:
        growth_mandate_service.set_status(product.id, GrowthMandateStatus.PAUSED)

    existing = store.get(TELEGRAM_PREVIEW_NAMESPACE, preview_key)
    if existing is not None and str(existing.get("status") or "") == "AWAITING_USER_APPROVAL":
        _validate_existing_preview(existing)
        return _render_preview(existing)

    connection = store.get(CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE, str(args.project_id))
    if connection is None or str(connection.get("status") or "").upper() != "ACTIVE":
        raise ValueError("An active customer-owned Telegram connection is required")

    identity = _ensure_customer_identity(args.project_id, product, connection)
    play = _generate_exact_play(product, distribution, identity, opportunity)

    with postgres_session_advisory_lock(AUTONOMOUS_GROWTH_ADVISORY_LOCK_KEY) as acquired:
        if not acquired:
            raise ValueError("Autonomous growth is already running; refusing concurrent preview")

        prepared_variants = await distribution_action_drafting_service.auto_prepare_variants(
            product=product,
            play=play,
        )
        if not prepared_variants:
            raise ValueError("No conversion-oriented Telegram variants were prepared")

        variant_records: list[dict] = []
        for prepared in prepared_variants:
            plan = prepared.plan
            _validate_draft(product, plan, target_url)
            if plan.action.status != DistributionActionStatus.PREPARED:
                raise ValueError("Human-preview action must remain PREPARED")

            mechanism = prepared.brief.conversion_mechanism.value
            variant_name = prepared.brief.variant_name
            content_text = str(plan.action.content_text or "")
            variant_records.append(
                {
                    "conversion_mechanism": mechanism,
                    "variant_name": variant_name,
                    "objective": prepared.brief.objective,
                    "expected_user_next_step": prepared.brief.expected_user_next_step,
                    "action_id": str(plan.action.id),
                    "content_sha256": _content_hash(
                        target_url,
                        mechanism,
                        variant_name,
                        content_text,
                    ),
                }
            )

        record = {
            "schema_version": PREVIEW_SCHEMA_VERSION,
            "status": "AWAITING_USER_APPROVAL",
            "project_id": str(args.project_id),
            "product_id": str(product.id),
            "opportunity_id": str(opportunity.id),
            "play_id": str(play.id),
            "target_url": target_url,
            "variants": variant_records,
            "prepared_at": datetime.now(UTC).isoformat(),
        }
        store.put(TELEGRAM_PREVIEW_NAMESPACE, preview_key, record)
        return _render_preview(record)


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = asyncio.run(run(args))
        print(json.dumps(result, ensure_ascii=False, default=str))
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
