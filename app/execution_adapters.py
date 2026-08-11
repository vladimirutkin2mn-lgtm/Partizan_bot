from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl

from app.distribution_execution_schemas import (
    DistributionActionExecutionRequest,
    DistributionExecutionPlanView,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_schemas import DistributionActionView
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

EXECUTION_ADAPTER_RECEIPT_NAMESPACE = "distribution_execution_adapter_receipt"


class AdapterExecutionOutcome(StrEnum):
    EXECUTED = "EXECUTED"
    ASSISTED = "ASSISTED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class DistributionAdapterExecuteRequest(BaseModel):
    retry: bool = False


class ExecutionAdapterReceipt(BaseModel):
    action_id: UUID
    adapter_name: str = Field(min_length=1, max_length=120)
    provider: str = Field(min_length=1, max_length=120)
    outcome: AdapterExecutionOutcome
    message: str = Field(min_length=1, max_length=2000)
    requires_operator_confirmation: bool = False
    external_reference: str | None = Field(default=None, max_length=500)
    executed_url: HttpUrl | None = None
    metadata: dict = Field(default_factory=dict)
    created_at: datetime


class DistributionAdapterExecutionView(BaseModel):
    receipt: ExecutionAdapterReceipt
    plan: DistributionExecutionPlanView


class ExecutionAdapter(Protocol):
    name: str
    provider: str

    def supports(self, action: DistributionActionView) -> bool: ...

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt: ...


class AssistedCommunityExecutionAdapter:
    name = "assisted-community"
    provider = "operator"

    _ACTION_TYPES = {
        DistributionActionType.COMMENT,
        DistributionActionType.REPLY,
        DistributionActionType.STANDALONE_POST,
    }

    def supports(self, action: DistributionActionView) -> bool:
        return action.action_type in self._ACTION_TYPES

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.ASSISTED,
            message=(
                "This third-party community action is prepared and approved, but the current "
                "MVP has no verified universal platform execution path for this target. Complete "
                "the action through the approved operator flow, then use mark-executed with the "
                "real external reference."
            ),
            requires_operator_confirmation=True,
            metadata={
                "platform": action.platform.value,
                "action_type": action.action_type.value,
                "target_url": str(action.target_url) if action.target_url else None,
            },
            created_at=datetime.now(UTC),
        )


class UnavailableOwnedExecutionAdapter:
    name = "owned-content-unavailable"
    provider = "not-configured"

    def supports(self, action: DistributionActionView) -> bool:
        return action.action_type == DistributionActionType.ORGANIC_VIDEO

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.UNAVAILABLE,
            message=(
                "No compliant owned-content execution provider is configured for this identity. "
                "For TikTok, Direct Post must use a connected creator flow and the required "
                "platform approval/audit before public automated posting is treated as supported."
            ),
            metadata={
                "platform": action.platform.value,
                "action_type": action.action_type.value,
            },
            created_at=datetime.now(UTC),
        )


class UnavailablePaidExecutionAdapter:
    name = "paid-campaign-unavailable"
    provider = "not-configured"

    def supports(self, action: DistributionActionView) -> bool:
        return action.action_type == DistributionActionType.PAID_CAMPAIGN

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.UNAVAILABLE,
            message=(
                "The paid campaign is prepared and approved, but no authenticated ad-platform "
                "provider is configured. Partizan will not create spend or mark the action "
                "executed until a provider confirms campaign creation."
            ),
            metadata={
                "platform": action.platform.value,
                "action_type": action.action_type.value,
            },
            created_at=datetime.now(UTC),
        )


class ConfirmedMockExecutionAdapter:
    """Deterministic adapter used only by tests/local integration harnesses."""

    name = "confirmed-mock"
    provider = "mock"

    def supports(self, action: DistributionActionView) -> bool:
        return True

    def execute(self, action: DistributionActionView) -> ExecutionAdapterReceipt:
        external_reference = f"mock:{action.platform.value.lower()}:{action.id}"
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name=self.name,
            provider=self.provider,
            outcome=AdapterExecutionOutcome.EXECUTED,
            message="Mock provider confirmed execution.",
            external_reference=external_reference,
            executed_url=f"https://execution.example/{action.id}",
            metadata={"test_only": True},
            created_at=datetime.now(UTC),
        )


class ExecutionAdapterRegistry:
    def __init__(self, adapters: list[ExecutionAdapter] | None = None) -> None:
        self._adapters = adapters or [
            AssistedCommunityExecutionAdapter(),
            UnavailableOwnedExecutionAdapter(),
            UnavailablePaidExecutionAdapter(),
        ]

    def resolve(self, action: DistributionActionView) -> ExecutionAdapter:
        for adapter in self._adapters:
            if adapter.supports(action):
                return adapter
        raise ValueError(
            f"No execution adapter registered for {action.platform.value}/{action.action_type.value}"
        )


class DistributionExecutionAdapterService:
    def __init__(
        self,
        registry: ExecutionAdapterRegistry | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._registry = registry or ExecutionAdapterRegistry()
        self._store = store or get_runtime_store()

    def execute(
        self,
        action_id: UUID,
        payload: DistributionAdapterExecuteRequest,
    ) -> DistributionAdapterExecutionView:
        action = distribution_execution_service.get_action(action_id)

        existing = self.get_receipt(action_id)
        if existing is not None and not payload.retry:
            return DistributionAdapterExecutionView(
                receipt=existing,
                plan=distribution_execution_service.get_plan(action_id),
            )

        if action.status == DistributionActionStatus.EXECUTED:
            if existing is None:
                existing = self._operator_confirmed_receipt(action)
                self._persist(existing)
            return DistributionAdapterExecutionView(
                receipt=existing,
                plan=distribution_execution_service.get_plan(action_id),
            )

        if action.status != DistributionActionStatus.APPROVED:
            raise ValueError("Action must be APPROVED before an execution adapter can run")

        adapter = self._registry.resolve(action)
        receipt = adapter.execute(action)
        if receipt.action_id != action.id:
            raise ValueError("Execution adapter returned a receipt for a different action")
        self._persist(receipt)

        if receipt.outcome == AdapterExecutionOutcome.EXECUTED:
            plan = distribution_execution_service.mark_executed(
                action.id,
                DistributionActionExecutionRequest(
                    external_reference=receipt.external_reference,
                    executed_url=receipt.executed_url,
                    notes=(
                        f"Confirmed by execution adapter {receipt.adapter_name} "
                        f"({receipt.provider})."
                    ),
                ),
            )
        else:
            plan = distribution_execution_service.get_plan(action.id)

        return DistributionAdapterExecutionView(receipt=receipt, plan=plan)

    def get_receipt(self, action_id: UUID) -> ExecutionAdapterReceipt | None:
        payload = self._store.get(EXECUTION_ADAPTER_RECEIPT_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return ExecutionAdapterReceipt.model_validate(payload)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(EXECUTION_ADAPTER_RECEIPT_NAMESPACE)

    def _persist(self, receipt: ExecutionAdapterReceipt) -> None:
        self._store.put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(receipt.action_id),
            receipt.model_dump(mode="json"),
        )

    def _operator_confirmed_receipt(
        self,
        action: DistributionActionView,
    ) -> ExecutionAdapterReceipt:
        metadata = action.operational_metadata
        executed_url = metadata.get("executed_url")
        return ExecutionAdapterReceipt(
            action_id=action.id,
            adapter_name="operator-confirmed",
            provider="operator",
            outcome=AdapterExecutionOutcome.EXECUTED,
            message="Action had already been confirmed as executed by the operator flow.",
            requires_operator_confirmation=False,
            external_reference=metadata.get("external_reference"),
            executed_url=executed_url,
            metadata={"reconstructed": True},
            created_at=action.executed_at or datetime.now(UTC),
        )


distribution_execution_adapter_service = DistributionExecutionAdapterService()
