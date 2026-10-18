"""Owner-scoped, read-only Telegram discovery progress for the customer workspace.

Do not expose internal billing, Telegram sessions, publisher identities,
provider exception messages or writeable action targets.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.autonomous_opportunity_refresh import AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_discovery_stage_queue import TelegramDiscoveryStageQueue

_STAGE_LABELS = {
    "registry": "Saved communities",
    "native": "Telegram search",
    "catalog": "Public catalog",
    "paid": "Optional web search",
}
_PUBLIC_SOURCES = {
    "REGISTRY_NATIVE_REFRESH": "Saved Telegram communities",
    "TELETHON_NATIVE": "Telegram",
    "TGSTAT_PUBLIC": "TGStat public catalog",
    "OPENAI_WEB": "Optional web research",
}
_PUBLIC_REASON_TEXT = {
    "EMPTY_REGISTRY": "No saved communities to check yet",
    "PEER_INSPECTION_COMPLETED": "Saved communities checked",
    "NO_ELIGIBLE_REFRESH_BATCH": "No saved communities ready for another check",
    "NATIVE_PEER_INSPECTION_FAILED": "Telegram community checks could not finish",
    "TELEGRAM_FLOOD_WAIT": "Telegram requested a pause",
    "VERIFIED_REGISTRY_COVERAGE": "Enough suitable saved communities",
    "NATIVE_CANDIDATES_FOUND": "Telegram returned community candidates",
    "NO_FIT_MATCH": "No community passed the relevance checks",
    "NATIVE_QUERY_ALREADY_ATTEMPTED": "Recent Telegram query is on cooldown",
    "NATIVE_RESEARCH_UNAVAILABLE": "Telegram research is unavailable",
    "NATIVE_QUERY_NOT_PLANNED": "No new Telegram query was planned",
    "STAGED_UNVERIFIED": "Catalog communities await Telegram verification",
    "NATIVE_COVERAGE": "Native search supplied sufficient coverage",
    "DISABLED": "This optional source is turned off",
    "NO_CATEGORY": "No suitable public catalog category",
    "NOT_DUE": "Waiting for the next permitted catalog check",
    "ALREADY_CLAIMED": "Catalog request already reserved",
    "SOURCE_THROTTLED": "Public catalog requested a pause",
    "FORMAT_UNKNOWN": "Catalog response could not be read",
    "SOURCE_UNAVAILABLE": "Public catalog is temporarily unavailable",
    "SOURCE_HTTP_ERROR": "Public catalog did not respond successfully",
    "NO_VALID_COMMUNITIES": "No valid public communities in the catalog response",
    "RETRYABLE_FAILED": "Research source failed; can retry later",
    "PAID_SEARCH_DISABLED": "Optional paid research is turned off",
    "NATIVE_SEARCH_NOT_COMPLETED": "Waiting for a completed Telegram search",
    "CATALOG_NOT_EXHAUSTED": "Waiting for the public catalog stage",
    "COST_ENVELOPE_NOT_CONFIGURED": "Optional research budget is not configured",
    "MONTHLY_BUDGET_EXHAUSTED": "Optional research budget is exhausted",
    "MONTHLY_BUDGET_INSUFFICIENT": "Optional research budget is insufficient",
    "DAILY_QUERY_LIMIT": "Daily optional research limit reached",
    "COMPLETED_BILLING_UNKNOWN": "Optional web search completed",
    "PROVIDER_FAILED_CHARGE_UNKNOWN": "Optional web provider could not finish",
    "NOT_APPLICABLE": "Not needed in this run",
    "NO_REGISTERED_HANDLES": "No saved communities",
    "OPERATOR_DISABLED": "Source is disabled",
}


class CustomerTelegramDiscoveryStageView(BaseModel):
    stage: str
    label: str
    status: str
    detail: str
    source: str | None = None
    search_query: str | None = None
    candidate_count: int = Field(ge=0)
    next_attempt_at: datetime | None = None


class CustomerTelegramDiscoveryView(BaseModel):
    status: str
    last_run_at: datetime | None = None
    last_run_complete: bool = False
    next_refresh_at: datetime | None = None
    next_refresh_reason: str | None = None
    stages: list[CustomerTelegramDiscoveryStageView] = Field(default_factory=list)


class CustomerTelegramDiscoveryService:
    def __init__(self, store: RuntimeStateStore | None = None):
        self._store = store or get_runtime_store()

    def for_project(self, project_id: UUID) -> CustomerTelegramDiscoveryView:
        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id)) or {}
        if project.get("deleted_at"):
            return CustomerTelegramDiscoveryView(status="NOT_RUN")
        raw_product_id = project.get("product_id")
        try:
            product_id = UUID(str(raw_product_id))
        except (TypeError, ValueError):
            return CustomerTelegramDiscoveryView(status="NOT_RUN")

        snapshot = TelegramDiscoveryStageQueue.latest(self._store, product_id)
        refresh = self._store.get(
            AUTONOMOUS_OPPORTUNITY_REFRESH_NAMESPACE, str(product_id)
        ) or {}
        if not snapshot:
            return CustomerTelegramDiscoveryView(
                status="NOT_RUN",
                next_refresh_at=self._timestamp(refresh.get("next_attempt_at")),
                next_refresh_reason=self._refresh_reason(refresh),
            )

        stages = []
        for row in snapshot.get("stages", []):
            stage = str(row.get("stage") or "")
            if stage not in _STAGE_LABELS:
                continue
            reason = str(row.get("reason") or "")
            stage_status = str(row.get("status") or "PENDING")
            detail = (
                "This source has not been checked in this run"
                if stage_status == "PENDING"
                else _PUBLIC_REASON_TEXT.get(reason, self._fallback_detail(stage_status))
            )
            source_code = str(row.get("source") or "")
            query = str(row.get("query") or "").strip()
            stages.append(
                CustomerTelegramDiscoveryStageView(
                    stage=stage,
                    label=_STAGE_LABELS[stage],
                    status=stage_status,
                    detail=detail,
                    source=_PUBLIC_SOURCES.get(source_code),
                    search_query=query[:180] if query else None,
                    candidate_count=max(0, int(row.get("candidate_count") or 0)),
                    next_attempt_at=self._timestamp(row.get("next_attempt_at")),
                )
            )

        return CustomerTelegramDiscoveryView(
            status="COMPLETED" if snapshot.get("complete") else "IN_PROGRESS",
            last_run_at=self._timestamp(snapshot.get("started_at")),
            last_run_complete=bool(snapshot.get("complete")),
            next_refresh_at=self._timestamp(refresh.get("next_attempt_at")),
            next_refresh_reason=self._refresh_reason(refresh),
            stages=stages,
        )

    @staticmethod
    def _timestamp(value: object) -> datetime | None:
        try:
            result = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return result.replace(tzinfo=UTC) if result.tzinfo is None else result.astimezone(UTC)

    @staticmethod
    def _refresh_reason(refresh: dict) -> str | None:
        if refresh.get("status") == "FAILED":
            return "Retry after a temporary discovery error"
        if refresh.get("next_attempt_reason") == "AWAITING_SOURCE_RETRY":
            return "Waiting for a research source to become available"
        if refresh.get("next_attempt_at"):
            return "Next scheduled discovery check"
        return None

    @staticmethod
    def _fallback_detail(status: str) -> str:
        return {
            "COMPLETED": "Check completed",
            "NO_FIT": "No suitable new communities found",
            "STAGED_UNVERIFIED": "Candidate communities require verification",
            "FAILED_RETRYABLE": "Research encountered a temporary error",
            "DEFERRED_COOLDOWN": "Waiting before another request",
            "DEFERRED_RATE_LIMIT": "Source requested a pause",
            "SKIPPED_DISABLED": "Source disabled",
            "SKIPPED_SUFFICIENT": "Existing results are sufficient",
            "SKIPPED_NO_CANDIDATES": "No candidates for this stage",
            "BLOCKED": "Required conditions not met",
            "BUDGET_EXHAUSTED": "Configured optional research limit reached",
            "NOT_APPLICABLE": "Not needed in this run",
        }.get(status, "No additional details")


customer_telegram_discovery_service = CustomerTelegramDiscoveryService()
