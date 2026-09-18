from __future__ import annotations

from uuid import UUID

from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_execution_service import distribution_execution_service
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecutionView,
    ExecutionAdapterReceipt,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_governance import (
    CustomerTelegramClientPublishError,
    customer_telegram_governance_service,
)
from app.telegram_client_publishing import (
    TelegramClientPublishOutcome,
    TelegramPublishRequest,
)


class CustomerTelegramAutonomousExecutionService:
    """Route autonomous Telegram actions only through explicit client-owned authorization."""

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    async def execute(
        self,
        *,
        product_id: UUID,
        action_id: UUID,
        retry: bool = False,
    ) -> DistributionAdapterExecutionView:
        project_id = self._project_id_for_product(product_id)
        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None:
            raise CustomerTelegramClientPublishError("Customer project not found")
        preferences = project.get("channel_preferences")
        telegram_mode = (
            str(preferences.get("TELEGRAM") or "").strip().upper()
            if isinstance(preferences, dict)
            else ""
        )
        if telegram_mode != "AUTO":
            raise CustomerTelegramClientPublishError(
                "Telegram channel is not in AUTO mode for this project"
            )

        receipt = await customer_telegram_governance_service.automated_publish_internal(
            project_id,
            action_id,
            TelegramPublishRequest(retry=retry),
        )
        return DistributionAdapterExecutionView(
            receipt=ExecutionAdapterReceipt(
                action_id=receipt.action_id,
                adapter_name="telegram-client-owned-autonomous",
                provider="telegram-client-owned",
                outcome=self._outcome(receipt.outcome),
                message=receipt.message,
                external_reference=receipt.external_reference,
                executed_url=receipt.executed_url,
                metadata={"telegram_client_owned": True},
                created_at=receipt.created_at,
            ),
            plan=distribution_execution_service.get_plan(action_id),
        )

    def _project_id_for_product(self, product_id: UUID) -> UUID:
        matches: list[UUID] = []
        for project in self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE):
            if str(project.get("product_id") or "") != str(product_id):
                continue
            try:
                matches.append(UUID(str(project["id"])))
            except (KeyError, TypeError, ValueError):
                continue
        if len(matches) != 1:
            raise CustomerTelegramClientPublishError(
                "Autonomous Telegram execution requires exactly one customer project "
                "for this product"
            )
        return matches[0]

    @staticmethod
    def _outcome(outcome: TelegramClientPublishOutcome) -> AdapterExecutionOutcome:
        return {
            TelegramClientPublishOutcome.IN_PROGRESS: AdapterExecutionOutcome.IN_PROGRESS,
            TelegramClientPublishOutcome.EXECUTED: AdapterExecutionOutcome.EXECUTED,
            TelegramClientPublishOutcome.FAILED: AdapterExecutionOutcome.FAILED,
            TelegramClientPublishOutcome.UNAVAILABLE: AdapterExecutionOutcome.UNAVAILABLE,
        }[outcome]


customer_telegram_autonomous_execution_service = (
    CustomerTelegramAutonomousExecutionService()
)
