from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.distribution_execution_service import DISTRIBUTION_ACTION_NAMESPACE
from app.distribution_schemas import DistributionActionView
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    ExecutionAdapterReceipt,
)
from app.meta_paid_control import (
    META_PAID_CONTROL_NAMESPACE,
    MetaPaidControlSnapshotView,
)
from app.paid_control_sweep import (
    PAID_CONTROL_SWEEP_RUN_NAMESPACE,
    PaidControlSweepItemOutcome,
    PaidControlSweepItemView,
    PaidControlSweepRegistry,
    PaidControlSweepView,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_paid_control import (
    TIKTOK_PAID_CONTROL_NAMESPACE,
    TikTokPaidControlSnapshotView,
)

type LatestSweepObservation = tuple[PaidControlSweepItemView, datetime]
type ControlSignal = tuple[
    bool,
    str,
    str | None,
    str | None,
    float | None,
    float | None,
    str | None,
    datetime,
]


class PaidReconciliationItemView(BaseModel):
    action_id: UUID
    experiment_id: UUID | None = None
    platform: DistributionPlatform
    action_status: DistributionActionStatus
    provider: str | None = None
    sources: list[str] = Field(min_length=1)
    reasons: list[str] = Field(min_length=1)
    provider_status: str | None = None
    provider_spend: float | None = Field(default=None, ge=0)
    synced_spend: float | None = Field(default=None, ge=0)
    pause_state: str | None = None
    last_seen_at: datetime


class PaidReconciliationQueueView(BaseModel):
    count: int = Field(ge=0)
    items: list[PaidReconciliationItemView]


class PaidReconcileResultView(BaseModel):
    action_id: UUID
    resolved: bool
    sync: PaidControlSweepItemView
    remaining: PaidReconciliationItemView | None = None


class PaidControlReconciliationService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        registry: PaidControlSweepRegistry | None = None,
        history_retention: int = 100,
    ) -> None:
        if history_retention < 1:
            raise ValueError("history_retention must be positive")
        self._store = store or get_runtime_store()
        self._registry = registry or PaidControlSweepRegistry()
        self._history_retention = history_retention

    def queue(self) -> PaidReconciliationQueueView:
        latest_sweep_items = self._latest_sweep_items()
        items: list[PaidReconciliationItemView] = []
        for action in self._paid_actions():
            item = self._build_item(action, latest_sweep_items.get(action.id))
            if item is not None:
                items.append(item)
        items.sort(key=lambda item: (item.last_seen_at, str(item.action_id)), reverse=True)
        return PaidReconciliationQueueView(count=len(items), items=items)

    def reconcile(self, action_id: UUID) -> PaidReconcileResultView:
        action = self._get_paid_action(action_id)
        receipt = self._get_receipt(action.id)
        if receipt is None:
            raise ValueError("Paid action has no provider receipt to reconcile")
        provider = self._registry.get(receipt.provider)
        if provider is None:
            raise ValueError(
                f"No supported paid-control provider is registered for {receipt.provider}"
            )
        try:
            sync = provider.sync(action.id)
        except (KeyError, ValueError, RuntimeError) as exc:
            sync = PaidControlSweepItemView(
                action_id=action.id,
                provider=receipt.provider,
                outcome=PaidControlSweepItemOutcome.ERROR,
                requires_reconciliation=True,
                reason=str(exc)[:1000],
            )
        observed_at = datetime.now(UTC)
        self._persist_reconcile_observation(sync, observed_at)
        remaining = self._build_item(action, (sync, observed_at))
        return PaidReconcileResultView(
            action_id=action.id,
            resolved=remaining is None,
            sync=sync,
            remaining=remaining,
        )

    def _paid_actions(self) -> list[DistributionActionView]:
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
        return actions

    def _get_paid_action(self, action_id: UUID) -> DistributionActionView:
        payload = self._store.get(DISTRIBUTION_ACTION_NAMESPACE, str(action_id))
        if payload is None:
            raise KeyError(action_id)
        action = DistributionActionView.model_validate(payload)
        if action.action_type != DistributionActionType.PAID_CAMPAIGN:
            raise ValueError("Reconciliation supports PAID_CAMPAIGN actions only")
        if action.status not in {
            DistributionActionStatus.APPROVED,
            DistributionActionStatus.EXECUTED,
        }:
            raise ValueError("Paid action is not in a controllable lifecycle state")
        return action

    def _build_item(
        self,
        action: DistributionActionView,
        latest_observation: LatestSweepObservation | None,
    ) -> PaidReconciliationItemView | None:
        latest_sweep = latest_observation[0] if latest_observation else None
        receipt = self._get_receipt(action.id)
        sources: list[str] = []
        reasons: list[str] = []
        provider = receipt.provider if receipt else None
        if provider is None and latest_sweep is not None:
            provider = latest_sweep.provider
        provider_status: str | None = None
        provider_spend: float | None = None
        synced_spend: float | None = None
        pause_state: str | None = None
        last_seen = action.executed_at or datetime.min.replace(tzinfo=UTC)

        if receipt is not None and receipt.metadata.get("requires_reconciliation"):
            sources.append("EXECUTION_RECEIPT")
            receipt_reason = (
                receipt.metadata.get("activation_error")
                or receipt.metadata.get("last_error")
                or receipt.message
            )
            self._append_unique(reasons, str(receipt_reason)[:1000])
            last_seen = max(last_seen, receipt.created_at)

        control = self._control_signal(action, receipt)
        if control is not None:
            requires, source, reason, status, provider_spend, synced_spend, pause_state, seen = control
            provider_status = status
            last_seen = max(last_seen, seen)
            if requires:
                sources.append(source)
                if reason:
                    self._append_unique(reasons, reason)

        if latest_observation is not None and latest_sweep is not None:
            last_seen = max(last_seen, latest_observation[1])
            provider = provider or latest_sweep.provider
            provider_status = latest_sweep.provider_status or provider_status
            provider_spend = (
                latest_sweep.provider_spend
                if latest_sweep.provider_spend is not None
                else provider_spend
            )
            synced_spend = (
                latest_sweep.synced_spend
                if latest_sweep.synced_spend is not None
                else synced_spend
            )
            pause_state = latest_sweep.pause_state or pause_state
            if (
                latest_sweep.outcome == PaidControlSweepItemOutcome.ERROR
                or latest_sweep.requires_reconciliation
            ):
                sources.append("LATEST_SWEEP")
                if latest_sweep.reason:
                    self._append_unique(reasons, latest_sweep.reason)

        if not sources:
            return None
        if not reasons:
            reasons.append("Provider state requires reconciliation")
        return PaidReconciliationItemView(
            action_id=action.id,
            experiment_id=action.experiment_id,
            platform=action.platform,
            action_status=action.status,
            provider=provider,
            sources=self._dedupe(sources),
            reasons=reasons,
            provider_status=provider_status,
            provider_spend=provider_spend,
            synced_spend=synced_spend,
            pause_state=pause_state,
            last_seen_at=last_seen if last_seen.year > 1 else datetime.now(UTC),
        )

    def _control_signal(
        self,
        action: DistributionActionView,
        receipt: ExecutionAdapterReceipt | None,
    ) -> ControlSignal | None:
        provider = receipt.provider if receipt else None
        if provider == "meta-marketing-api":
            payload = self._store.get(META_PAID_CONTROL_NAMESPACE, str(action.id))
            if payload is None:
                return None
            snapshot = MetaPaidControlSnapshotView.model_validate(payload)
            requires = (
                snapshot.requires_reconciliation
                or snapshot.sync_state == "UNKNOWN"
                or snapshot.pause_state == "UNKNOWN"
            )
            status = f"{snapshot.configured_status}/{snapshot.effective_status}"
            return (
                requires,
                "META_CONTROL",
                snapshot.last_error,
                status,
                snapshot.provider_spend,
                snapshot.synced_spend,
                snapshot.pause_state,
                snapshot.synced_at,
            )
        if provider == "tiktok-marketing-api":
            payload = self._store.get(TIKTOK_PAID_CONTROL_NAMESPACE, str(action.id))
            if payload is None:
                return None
            snapshot = TikTokPaidControlSnapshotView.model_validate(payload)
            requires = (
                snapshot.requires_reconciliation
                or snapshot.sync_state == "UNKNOWN"
                or snapshot.pause_state == "UNKNOWN"
            )
            status_parts = [snapshot.operation_status]
            if snapshot.primary_status:
                status_parts.append(snapshot.primary_status)
            if snapshot.secondary_status:
                status_parts.append(snapshot.secondary_status)
            return (
                requires,
                "TIKTOK_CONTROL",
                snapshot.last_error,
                "/".join(status_parts),
                snapshot.provider_spend,
                snapshot.synced_spend,
                snapshot.pause_state,
                snapshot.synced_at,
            )
        return None

    def _latest_sweep_items(self) -> dict[UUID, LatestSweepObservation]:
        runs = [
            PaidControlSweepView.model_validate(payload)
            for payload in self._store.list_namespace(PAID_CONTROL_SWEEP_RUN_NAMESPACE)
        ]
        runs.sort(key=lambda run: (run.finished_at, str(run.run_id)), reverse=True)
        latest: dict[UUID, LatestSweepObservation] = {}
        for run in runs:
            for item in run.items:
                latest.setdefault(item.action_id, (item, run.finished_at))
        return latest

    def _persist_reconcile_observation(
        self,
        sync: PaidControlSweepItemView,
        observed_at: datetime,
    ) -> None:
        run = PaidControlSweepView(
            run_id=uuid4(),
            started_at=observed_at,
            finished_at=observed_at,
            candidate_count=1,
            synced_count=int(sync.outcome == PaidControlSweepItemOutcome.SYNCED),
            skipped_count=int(sync.outcome == PaidControlSweepItemOutcome.SKIPPED),
            error_count=int(sync.outcome == PaidControlSweepItemOutcome.ERROR),
            items=[sync],
        )
        self._store.put(
            PAID_CONTROL_SWEEP_RUN_NAMESPACE,
            str(run.run_id),
            run.model_dump(mode="json"),
        )
        self._trim_history()

    def _trim_history(self) -> None:
        runs = [
            PaidControlSweepView.model_validate(payload)
            for payload in self._store.list_namespace(PAID_CONTROL_SWEEP_RUN_NAMESPACE)
        ]
        if len(runs) <= self._history_retention:
            return
        runs.sort(key=lambda run: (run.finished_at, str(run.run_id)), reverse=True)
        for stale in runs[self._history_retention :]:
            self._store.delete(PAID_CONTROL_SWEEP_RUN_NAMESPACE, str(stale.run_id))

    def _get_receipt(self, action_id: UUID) -> ExecutionAdapterReceipt | None:
        payload = self._store.get(EXECUTION_ADAPTER_RECEIPT_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return ExecutionAdapterReceipt.model_validate(payload)

    def _append_unique(self, values: list[str], value: str) -> None:
        normalized = value.strip()
        if normalized and normalized not in values:
            values.append(normalized)

    def _dedupe(self, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


paid_control_reconciliation_service = PaidControlReconciliationService()
