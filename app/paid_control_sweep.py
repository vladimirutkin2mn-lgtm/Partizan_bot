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
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_paid_control import TikTokPaidControlService, tiktok_paid_control_service


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
            provider_status=(
                f"{snapshot.configured_status}/{snapshot.effective_status}"
            ),
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
        configured = providers or [
            MetaPaidControlSweepProvider(),
            TikTokPaidControlSweepProvider(),
        ]
        self._providers = {provider.provider: provider for provider in configured}

    def get(self, provider: str) -> PaidControlSweepProvider | None:
        return self._providers.get(provider)


class PaidControlSweepService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        registry: PaidControlSweepRegistry | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._registry = registry or PaidControlSweepRegistry()
        self._lock = Lock()

    @property
    def store(self) -> RuntimeStateStore:
        return self._store

    def run_once(self) -> PaidControlSweepView:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A paid-control sweep is already running in this process")
        started_at = datetime.now(UTC)
        try:
            items = [self._run_action(action) for action in self._candidate_actions()]
        finally:
            self._lock.release()
        finished_at = datetime.now(UTC)
        return PaidControlSweepView(
            run_id=uuid4(),
            started_at=started_at,
            finished_at=finished_at,
            candidate_count=len(items),
            synced_count=sum(item.outcome == PaidControlSweepItemOutcome.SYNCED for item in items),
            skipped_count=sum(item.outcome == PaidControlSweepItemOutcome.SKIPPED for item in items),
            error_count=sum(item.outcome == PaidControlSweepItemOutcome.ERROR for item in items),
            items=items,
        )

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

    def _run_action(self, action: DistributionActionView) -> PaidControlSweepItemView:
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
        try:
            return provider.sync(action.id)
        except (KeyError, ValueError, RuntimeError) as exc:
            return PaidControlSweepItemView(
                action_id=action.id,
                provider=receipt.provider,
                outcome=PaidControlSweepItemOutcome.ERROR,
                requires_reconciliation=True,
                reason=str(exc)[:1000],
            )


paid_control_sweep_service = PaidControlSweepService()
