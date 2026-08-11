from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.distribution_execution_service import DISTRIBUTION_ACTION_NAMESPACE
from app.distribution_schemas import DistributionActionView
from app.distribution_types import DistributionActionStatus, DistributionActionType
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    ExecutionAdapterReceipt,
)
from app.meta_paid_control import MetaPaidControlService, meta_paid_control_service
from app.paid_lifecycle_audit import (
    PaidAuditActor,
    PaidAuditEventType,
    PaidAuditLedger,
    PaidAuditResult,
    PaidLifecycleService,
    PaidLifecycleState,
    PaidLifecycleView,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_paid_control import TikTokPaidControlService, tiktok_paid_control_service

PAID_CONTROL_SWEEP_RUN_NAMESPACE = "paid_control_sweep_run"


class PaidControlSweepItemOutcome(StrEnum):
    SYNCED = "SYNCED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class PaidControlSweepItemView(BaseModel):
    action_id: UUID
    provider: str | None = None
    outcome: PaidControlSweepItemOutcome
    provider_status: str | None = None
    provider_spend: float | None = Field(default=None, ge=0)
    synced_spend: float | None = Field(default=None, ge=0)
    pause_state: str | None = None
    requires_reconciliation: bool = False
    reason: str | None = None


class PaidControlSweepView(BaseModel):
    run_id: UUID
    started_at: datetime
    finished_at: datetime
    candidate_count: int = Field(ge=0)
    synced_count: int = Field(ge=0)
    skipped_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    items: list[PaidControlSweepItemView]


class PaidControlSweepProvider(Protocol):
    provider: str

    def sync(self, action_id: UUID) -> PaidControlSweepItemView: ...


class MetaPaidControlSweepProvider:
    provider = "meta-marketing-api"

    def __init__(self, service: MetaPaidControlService | None = None) -> None:
        self._service = service or meta_paid_control_service

    def sync(self, action_id: UUID) -> PaidControlSweepItemView:
        snapshot = self._service.sync(action_id)
        return PaidControlSweepItemView(
            action_id=action_id,
            provider=self.provider,
            outcome=PaidControlSweepItemOutcome.SYNCED,
            provider_status=f"{snapshot.configured_status}/{snapshot.effective_status}",
            provider_spend=snapshot.provider_spend,
            synced_spend=snapshot.synced_spend,
            pause_state=snapshot.pause_state,
            requires_reconciliation=snapshot.requires_reconciliation,
            reason=snapshot.last_error,
        )


class TikTokPaidControlSweepProvider:
    provider = "tiktok-marketing-api"

    def __init__(self, service: TikTokPaidControlService | None = None) -> None:
        self._service = service or tiktok_paid_control_service

    def sync(self, action_id: UUID) -> PaidControlSweepItemView:
        snapshot = self._service.sync(action_id)
        status_parts = [snapshot.operation_status]
        if snapshot.primary_status:
            status_parts.append(snapshot.primary_status)
        if snapshot.secondary_status:
            status_parts.append(snapshot.secondary_status)
        return PaidControlSweepItemView(
            action_id=action_id,
            provider=self.provider,
            outcome=PaidControlSweepItemOutcome.SYNCED,
            provider_status="/".join(status_parts),
            provider_spend=snapshot.provider_spend,
            synced_spend=snapshot.synced_spend,
            pause_state=snapshot.pause_state,
            requires_reconciliation=snapshot.requires_reconciliation,
            reason=snapshot.last_error,
        )


class PaidControlSweepRegistry:
    def __init__(self, providers: list[PaidControlSweepProvider] | None = None) -> None:
        configured = (
            [MetaPaidControlSweepProvider(), TikTokPaidControlSweepProvider()]
            if providers is None
            else providers
        )
        self._providers = {provider.provider: provider for provider in configured}

    def get(self, provider: str) -> PaidControlSweepProvider | None:
        return self._providers.get(provider)


class PaidControlSweepService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        registry: PaidControlSweepRegistry | None = None,
        history_retention: int = 100,
        lifecycle_service: PaidLifecycleService | None = None,
        audit_ledger: PaidAuditLedger | None = None,
    ) -> None:
        if history_retention < 1:
            raise ValueError("history_retention must be positive")
        self._store = store or get_runtime_store()
        self._registry = registry or PaidControlSweepRegistry()
        self._history_retention = history_retention
        self._lifecycle = lifecycle_service or PaidLifecycleService(self._store)
        self._audit = audit_ledger or PaidAuditLedger(self._store, self._lifecycle)
        self._lock = Lock()

    @property
    def store(self) -> RuntimeStateStore:
        return self._store

    def run_once(self) -> PaidControlSweepView:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A paid-control sweep is already running in this process")
        try:
            started_at = datetime.now(UTC)
            run_id = uuid4()
            items = [
                self._run_action(action, correlation_id=run_id)
                for action in self._candidate_actions()
            ]
            result = PaidControlSweepView(
                run_id=run_id,
                started_at=started_at,
                finished_at=datetime.now(UTC),
                candidate_count=len(items),
                synced_count=sum(
                    item.outcome == PaidControlSweepItemOutcome.SYNCED for item in items
                ),
                skipped_count=sum(
                    item.outcome == PaidControlSweepItemOutcome.SKIPPED for item in items
                ),
                error_count=sum(
                    item.outcome == PaidControlSweepItemOutcome.ERROR for item in items
                ),
                items=items,
            )
            self._persist_run(result)
            return result
        finally:
            self._lock.release()

    def recent_runs(self, limit: int = 20) -> list[PaidControlSweepView]:
        if limit < 1 or limit > self._history_retention:
            raise ValueError(
                f"limit must be between 1 and {self._history_retention}"
            )
        runs = [
            PaidControlSweepView.model_validate(payload)
            for payload in self._store.list_namespace(PAID_CONTROL_SWEEP_RUN_NAMESPACE)
        ]
        runs.sort(key=lambda run: (run.finished_at, str(run.run_id)), reverse=True)
        return runs[:limit]

    def _candidate_actions(self) -> list[DistributionActionView]:
        actions: list[DistributionActionView] = []
        for payload in self._store.list_namespace(DISTRIBUTION_ACTION_NAMESPACE):
            action = DistributionActionView.model_validate(payload)
            if action.action_type != DistributionActionType.PAID_CAMPAIGN:
                continue
            if action.status not in {
                DistributionActionStatus.APPROVED,
                DistributionActionStatus.EXECUTED,
            }:
                continue
            actions.append(action)
        return sorted(actions, key=lambda action: str(action.id))

    def _run_action(
        self,
        action: DistributionActionView,
        *,
        correlation_id: UUID,
    ) -> PaidControlSweepItemView:
        receipt_payload = self._store.get(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(action.id),
        )
        if receipt_payload is None:
            return PaidControlSweepItemView(
                action_id=action.id,
                outcome=PaidControlSweepItemOutcome.SKIPPED,
                reason="Paid action has no execution-provider receipt yet",
            )
        receipt = ExecutionAdapterReceipt.model_validate(receipt_payload)
        provider = self._registry.get(receipt.provider)
        if provider is None:
            return PaidControlSweepItemView(
                action_id=action.id,
                provider=receipt.provider,
                outcome=PaidControlSweepItemOutcome.SKIPPED,
                reason="No autonomous paid-control provider is registered for this receipt",
            )

        before = self._observe(action.id)
        try:
            item = provider.sync(action.id)
        except (KeyError, ValueError, RuntimeError) as exc:
            item = PaidControlSweepItemView(
                action_id=action.id,
                provider=receipt.provider,
                outcome=PaidControlSweepItemOutcome.ERROR,
                requires_reconciliation=True,
                reason=str(exc)[:1000],
            )
        after = self._observe(action.id)
        self._record_audit(
            action_id=action.id,
            event_type=PaidAuditEventType.CONTROL_SYNC,
            actor=PaidAuditActor.WORKER,
            result=(
                PaidAuditResult.SUCCESS
                if item.outcome == PaidControlSweepItemOutcome.SYNCED
                and not item.requires_reconciliation
                else PaidAuditResult.FAILED
            ),
            before=before,
            after=after,
            correlation_id=correlation_id,
            reason=item.reason,
            deduplicate=True,
        )
        if (
            before is not None
            and after is not None
            and before.state != PaidLifecycleState.PAUSED
            and after.state == PaidLifecycleState.PAUSED
        ):
            self._record_audit(
                action_id=action.id,
                event_type=PaidAuditEventType.PROVIDER_PAUSE,
                actor=PaidAuditActor.WORKER,
                result=PaidAuditResult.SUCCESS,
                before=before,
                after=after,
                correlation_id=correlation_id,
                reason=after.pause_reason,
                deduplicate=True,
            )
        return item

    def _observe(self, action_id: UUID) -> PaidLifecycleView | None:
        try:
            return self._lifecycle.get(action_id)
        except Exception:
            return None

    def _record_audit(
        self,
        *,
        action_id: UUID,
        event_type: PaidAuditEventType,
        actor: PaidAuditActor,
        result: PaidAuditResult,
        before: PaidLifecycleView | None,
        after: PaidLifecycleView | None,
        correlation_id: UUID,
        reason: str | None,
        deduplicate: bool,
    ) -> None:
        try:
            self._audit.record(
                action_id=action_id,
                event_type=event_type,
                actor=actor,
                result=result,
                before=before,
                after=after,
                correlation_id=correlation_id,
                reason=reason,
                deduplicate=deduplicate,
            )
        except Exception:
            return

    def _persist_run(self, result: PaidControlSweepView) -> None:
        self._store.put(
            PAID_CONTROL_SWEEP_RUN_NAMESPACE,
            str(result.run_id),
            result.model_dump(mode="json"),
        )
        runs = [
            PaidControlSweepView.model_validate(payload)
            for payload in self._store.list_namespace(PAID_CONTROL_SWEEP_RUN_NAMESPACE)
        ]
        if len(runs) <= self._history_retention:
            return
        runs.sort(key=lambda run: (run.finished_at, str(run.run_id)), reverse=True)
        for stale in runs[self._history_retention :]:
            self._store.delete(PAID_CONTROL_SWEEP_RUN_NAMESPACE, str(stale.run_id))


paid_control_sweep_service = PaidControlSweepService()
