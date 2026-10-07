from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.product_intake import product_intake_service
from app.project_marketing_memory import (
    ProjectMarketingMemoryCustomerCreateRequest,
    ProjectMarketingMemoryRecordRequest,
    ProjectMarketingMemorySource,
    project_marketing_memory_service,
)
from app.runtime_store import get_runtime_store

PROJECT_MARKETING_MEMORY_SEED_NAMESPACE = "project_marketing_memory_seed"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Apply one reviewed, idempotent set of customer-confirmed marketing-memory "
            "entries to an exact Partizan project."
        )
    )
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID, required=True)
    parser.add_argument("--expected-product-name", required=True)
    parser.add_argument("--seed-id", required=True)
    parser.add_argument("--seed-file", type=Path, required=True)
    return parser


def _seed_key(project_id: UUID, seed_id: str) -> str:
    return f"{project_id}:{seed_id.strip()}"


def _load_entries(path: Path) -> list[ProjectMarketingMemoryCustomerCreateRequest]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Marketing-memory seed file must contain a non-empty JSON list")
    return [ProjectMarketingMemoryCustomerCreateRequest.model_validate(item) for item in payload]


def run(args: argparse.Namespace) -> dict:
    store = get_runtime_store()
    project = store.get(CUSTOMER_PROJECT_NAMESPACE, str(args.project_id))
    if project is None or project.get("deleted_at"):
        raise ValueError("Exact customer project is not available")
    if str(project.get("product_id") or "") != str(args.expected_product_id):
        raise ValueError("Customer project product id does not match the reviewed seed")

    product = product_intake_service.get_product(args.expected_product_id)
    if product.name.strip() != args.expected_product_name.strip():
        raise ValueError("Customer project product name does not match the reviewed seed")

    seed_id = args.seed_id.strip()
    if not seed_id:
        raise ValueError("Seed id is required")
    marker_key = _seed_key(args.project_id, seed_id)
    existing = store.get(PROJECT_MARKETING_MEMORY_SEED_NAMESPACE, marker_key)
    if existing is not None:
        return {**existing, "status": "ALREADY_APPLIED"}

    entries = _load_entries(args.seed_file)
    applied_ids: list[str] = []
    for item in entries:
        recorded = project_marketing_memory_service.record_internal(
            args.project_id,
            args.expected_product_id,
            ProjectMarketingMemoryRecordRequest(
                **item.model_dump(),
                source=ProjectMarketingMemorySource.CUSTOMER_CONFIRMED,
                confidence=1.0,
                source_ref=f"reviewed-seed:{seed_id}",
            ),
        )
        applied_ids.append(str(recorded.id))

    prompt = project_marketing_memory_service.prompt_context_for_product(
        args.expected_product_id,
        platform="TELEGRAM",
        action_type="COMMENT",
    )
    now = datetime.now(UTC).isoformat()
    marker = {
        "status": "APPLIED",
        "seed_id": seed_id,
        "project_id": str(args.project_id),
        "product_id": str(args.expected_product_id),
        "product_name": product.name,
        "seed_file": str(args.seed_file),
        "entry_ids": applied_ids,
        "entry_count": len(applied_ids),
        "telegram_comment_prompt_entry_count": prompt.entry_count,
        "applied_at": now,
    }
    if not store.put_if_absent(
        PROJECT_MARKETING_MEMORY_SEED_NAMESPACE,
        marker_key,
        marker,
    ):
        existing = store.get(PROJECT_MARKETING_MEMORY_SEED_NAMESPACE, marker_key)
        if existing is not None:
            return {**existing, "status": "ALREADY_APPLIED"}
        raise RuntimeError("Marketing-memory seed marker could not be persisted")
    return marker


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
