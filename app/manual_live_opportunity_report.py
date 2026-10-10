from __future__ import annotations

import argparse
import json
from collections import Counter
from uuid import UUID

from app.customer_live_opportunities import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerLiveOpportunityService,
    customer_live_opportunity_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store


class ManualLiveOpportunityReportService:
    """Build a read-only snapshot of current customer live opportunities.

    The reporter only reads runtime state. It does not preflight Telegram, join a
    community, publish, message, update a profile/story, or mutate opportunity rows.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        opportunity_service: CustomerLiveOpportunityService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._opportunity_service = opportunity_service or customer_live_opportunity_service

    def build(
        self,
        *,
        project_id: UUID,
        expected_product_id: UUID | None = None,
        platform: str | None = None,
        source: str | None = None,
        limit: int = 20,
    ) -> dict:
        self._validate_project(project_id, expected_product_id=expected_product_id)
        normalized_platform = str(platform or "").strip().upper()
        normalized_source = str(source or "").strip().upper()

        rows = [
            item
            for item in self._opportunity_service.list_for_project(project_id)
            if item.status == "ACTIVE" and item.freshness != "STALE"
        ]
        if normalized_platform:
            rows = [item for item in rows if item.platform.upper() == normalized_platform]
        if normalized_source:
            rows = [item for item in rows if item.source.upper() == normalized_source]

        rows.sort(
            key=lambda item: (
                -(item.relevance_score or 0.0),
                item.expires_at.isoformat() if item.expires_at else "9999",
                item.title.casefold(),
            )
        )
        selected = rows[: max(1, min(int(limit), 100))]

        return {
            "status": "READ_ONLY",
            "project_id": str(project_id),
            "expected_product_id": str(expected_product_id) if expected_product_id else None,
            "filters": {
                "platform": normalized_platform or None,
                "source": normalized_source or None,
            },
            "active_fresh_count": len(rows),
            "publishability": dict(sorted(Counter(item.publishability for item in rows).items())),
            "sources": dict(sorted(Counter(item.source for item in rows).items())),
            "score": self._score_summary(rows),
            "opportunities": [self._serialize(item) for item in selected],
        }

    def _validate_project(
        self,
        project_id: UUID,
        *,
        expected_product_id: UUID | None,
    ) -> None:
        matching = [
            row
            for row in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE)
            if str(row.get("id") or "") == str(project_id) and not row.get("deleted_at")
        ]
        if len(matching) != 1:
            raise ValueError(f"active customer project not found: {project_id}")
        if expected_product_id is not None and str(matching[0].get("product_id") or "") != str(
            expected_product_id
        ):
            raise ValueError("customer project product mismatch")

    @staticmethod
    def _score_summary(rows) -> dict:
        scores = [float(item.relevance_score) for item in rows if item.relevance_score is not None]
        if not scores:
            return {"count": 0, "average": None, "min": None, "max": None, "ge_70": 0}
        return {
            "count": len(scores),
            "average": round(sum(scores) / len(scores), 2),
            "min": round(min(scores), 2),
            "max": round(max(scores), 2),
            "ge_70": sum(score >= 70.0 for score in scores),
        }

    @staticmethod
    def _serialize(item) -> dict:
        return {
            "opportunity_id": item.opportunity_id,
            "source": item.source,
            "platform": item.platform,
            "kind": item.kind,
            "title": item.title,
            "url": str(item.url),
            "relevance_score": item.relevance_score,
            "publishability": item.publishability,
            "publishability_detail": item.publishability_detail,
            "freshness": item.freshness,
            "discovered_at": item.discovered_at.isoformat(),
            "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            "last_checked_at": item.last_checked_at.isoformat() if item.last_checked_at else None,
            "rationale": item.rationale,
            "recommended_action": item.recommended_action,
            "suggested_content": item.suggested_content,
        }


manual_live_opportunity_report_service = ManualLiveOpportunityReportService()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Print a read-only live opportunity report.")
    parser.add_argument("--project-id", type=UUID, required=True)
    parser.add_argument("--expected-product-id", type=UUID)
    parser.add_argument("--platform")
    parser.add_argument("--source")
    parser.add_argument("--limit", type=int, default=20)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        report = manual_live_opportunity_report_service.build(
            project_id=args.project_id,
            expected_product_id=args.expected_product_id,
            platform=args.platform,
            source=args.source,
            limit=args.limit,
        )
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
