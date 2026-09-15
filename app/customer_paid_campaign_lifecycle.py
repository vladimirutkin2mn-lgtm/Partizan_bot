from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.distribution_execution_schemas import DistributionExperimentView
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    DISTRIBUTION_EXPERIMENT_NAMESPACE,
)
from app.distribution_schemas import DistributionActionView
from app.distribution_types import DistributionActionStatus, DistributionActionType
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    EnvironmentSecretResolver,
    ExecutionAdapterReceipt,
    SecretResolver,
)
from app.meta_marketing_api import (
    HttpxMetaMarketingApiClient,
    MetaCampaignState,
    MetaMarketingApiClient,
    MetaMarketingApiError,
)
from app.meta_paid_control import (
    META_PAID_CONTROL_NAMESPACE,
    MetaPaidControlService,
    MetaPaidControlSnapshotView,
    meta_paid_control_service,
)
from app.paid_control_resume_boundary import (
    paid_control_resume_scope,
    require_paid_control_resume_scope,
)
from app.paid_provider_connections import PaidProviderConnectionService
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_marketing_api import (
    HttpxTikTokMarketingApiClient,
    TikTokCampaignState,
    TikTokMarketingApiClient,
    TikTokMarketingApiError,
)
from app.tiktok_paid_control import (
    TIKTOK_PAID_CONTROL_NAMESPACE,
    TikTokPaidControlService,
    TikTokPaidControlSnapshotView,
    tiktok_paid_control_service,
)
from app.tiktok_paid_provider import TikTokPaidProviderConnectionService

AUTOPILOT_CUSTOMER_PAUSE_REASON = "AUTOPILOT_CUSTOMER_PAUSE"


class CustomerPaidCampaignLifecycleResult(BaseModel):
    product_id: UUID
    candidate_count: int = Field(ge=0)
    provider_mutation_count: int = Field(ge=0)
    customer_paused_action_ids: list[UUID] = Field(default_factory=list)
    preserved_pause_action_ids: list[UUID] = Field(default_factory=list)
    resumed_action_ids: list[UUID] = Field(default_factory=list)
    reconciliation_action_ids: list[UUID] = Field(default_factory=list)
    rollback_unknown_action_ids: list[UUID] = Field(default_factory=list)

    @property
    def requires_reconciliation(self) -> bool:
        return bool(self.reconciliation_action_ids or self.rollback_unknown_action_ids)


class CustomerPaidProviderController(Protocol):
    provider: str

    def get(self, action_id: UUID): ...

    def pause(self, action_id: UUID, *, reason: str): ...

    def resume_customer_pause(self, action_id: UUID, *, expected_reason: str): ...


class MetaCustomerPaidProviderController:
    provider = "meta-marketing-api"

    def __init__(
        self,
        store: RuntimeStateStore,
        *,
        control_service: MetaPaidControlService | None = None,
        client: MetaMarketingApiClient | None = None,
        secret_resolver: SecretResolver | None = None,
        connection_service: PaidProviderConnectionService | None = None,
    ) -> None:
        self._store = store
        self._control = control_service or meta_paid_control_service
        self._client = client or HttpxMetaMarketingApiClient()
        self._secrets = secret_resolver or EnvironmentSecretResolver()
        self._connections = connection_service or PaidProviderConnectionService(store)

    def get(self, action_id: UUID) -> MetaPaidControlSnapshotView | None:
        payload = self._store.get(META_PAID_CONTROL_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return MetaPaidControlSnapshotView.model_validate(payload)

    def pause(self, action_id: UUID, *, reason: str) -> MetaPaidControlSnapshotView:
        return self._control.pause(action_id, reason=reason)

    def resume_customer_pause(
        self,
        action_id: UUID,
        *,
        expected_reason: str,
    ) -> MetaPaidControlSnapshotView:
        require_paid_control_resume_scope(action_id)
        previous = self.get(action_id)
        if previous is None:
            raise ValueError("Meta paid campaign has no control snapshot to resume")
        self._require_exact_customer_pause(previous, expected_reason)
        connection = self._connections.require_active_meta(previous.product_id)
        access_token = self._secrets.resolve(connection.access_token_env)
        if access_token is None:
            raise ValueError(
                f"Meta access-token secret {connection.access_token_env} is not available"
            )
        now = datetime.now(UTC)
        try:
            current = self._client.get_campaign_state(
                connection=connection,
                access_token=access_token,
                campaign_id=previous.campaign_id,
            )
            if current.configured_status.upper() != "PAUSED":
                return self._resume_unknown(
                    previous,
                    current,
                    "Meta campaign drifted from the confirmed customer pause before resume",
                    now,
                )
            self._client.set_status(
                connection=connection,
                access_token=access_token,
                object_id=previous.campaign_id,
                status="ACTIVE",
            )
            verified = self._client.get_campaign_state(
                connection=connection,
                access_token=access_token,
                campaign_id=previous.campaign_id,
            )
        except MetaMarketingApiError as exc:
            return self._resume_unknown(previous, None, str(exc)[:1000], now)
        if verified.configured_status.upper() != "ACTIVE":
            return self._resume_unknown(
                previous,
                verified,
                "Meta did not confirm configured_status=ACTIVE after customer resume",
                now,
            )
        resumed = previous.model_copy(
            update={
                "configured_status": verified.configured_status,
                "effective_status": verified.effective_status,
                "pause_state": "NOT_REQUESTED",
                "pause_reason": None,
                "requires_reconciliation": False,
                "last_error": None,
                "synced_at": now,
                "paused_at": None,
            }
        )
        self._persist(resumed, spend_state="ACTIVE")
        return resumed

    def _require_exact_customer_pause(
        self,
        snapshot: MetaPaidControlSnapshotView,
        expected_reason: str,
    ) -> None:
        if snapshot.pause_state != "CONFIRMED":
            raise ValueError("Meta customer pause is not provider-confirmed")
        if snapshot.pause_reason != expected_reason:
            raise ValueError("Meta campaign pause reason is not customer-resumeable")
        if snapshot.requires_reconciliation:
            raise ValueError("Meta paid campaign requires reconciliation before resume")
        if snapshot.budget_guardrail_triggered:
            raise ValueError("Meta budget guardrail pause cannot be customer-resumed")

    def _resume_unknown(
        self,
        previous: MetaPaidControlSnapshotView,
        state: MetaCampaignState | None,
        error: str,
        now: datetime,
    ) -> MetaPaidControlSnapshotView:
        unknown = previous.model_copy(
            update={
                "configured_status": state.configured_status if state else previous.configured_status,
                "effective_status": state.effective_status if state else previous.effective_status,
                "pause_state": "UNKNOWN",
                "requires_reconciliation": True,
                "last_error": error[:1000],
                "synced_at": now,
            }
        )
        self._persist(unknown, spend_state="UNKNOWN")
        return unknown

    def _persist(self, snapshot: MetaPaidControlSnapshotView, *, spend_state: str) -> None:
        self._store.put(
            META_PAID_CONTROL_NAMESPACE,
            str(snapshot.action_id),
            snapshot.model_dump(mode="json"),
        )
        receipt = self._receipt(snapshot.action_id)
        metadata = dict(receipt.metadata)
        metadata["provider_control"] = {
            "configured_status": snapshot.configured_status,
            "effective_status": snapshot.effective_status,
            "provider_spend": snapshot.provider_spend,
            "synced_spend": snapshot.synced_spend,
            "account_currency": snapshot.account_currency,
            "pause_state": snapshot.pause_state,
            "pause_reason": snapshot.pause_reason,
            "synced_at": snapshot.synced_at.isoformat(),
        }
        metadata["spend_state"] = spend_state
        metadata["requires_reconciliation"] = snapshot.requires_reconciliation
        self._store.put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(snapshot.action_id),
            receipt.model_copy(update={"metadata": metadata}).model_dump(mode="json"),
        )

    def _receipt(self, action_id: UUID) -> ExecutionAdapterReceipt:
        payload = self._store.get(EXECUTION_ADAPTER_RECEIPT_NAMESPACE, str(action_id))
        if payload is None:
            raise ValueError("Meta paid campaign has no execution receipt")
        return ExecutionAdapterReceipt.model_validate(payload)


class TikTokCustomerPaidProviderController:
    provider = "tiktok-marketing-api"

    def __init__(
        self,
        store: RuntimeStateStore,
        *,
        control_service: TikTokPaidControlService | None = None,
        client: TikTokMarketingApiClient | None = None,
        secret_resolver: SecretResolver | None = None,
        connection_service: TikTokPaidProviderConnectionService | None = None,
    ) -> None:
        self._store = store
        self._control = control_service or tiktok_paid_control_service
        self._client = client or HttpxTikTokMarketingApiClient()
        self._secrets = secret_resolver or EnvironmentSecretResolver()
        self._connections = connection_service or TikTokPaidProviderConnectionService(store)

    def get(self, action_id: UUID) -> TikTokPaidControlSnapshotView | None:
        payload = self._store.get(TIKTOK_PAID_CONTROL_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return TikTokPaidControlSnapshotView.model_validate(payload)

    def pause(self, action_id: UUID, *, reason: str) -> TikTokPaidControlSnapshotView:
        return self._control.pause(action_id, reason=reason)

    def resume_customer_pause(
        self,
        action_id: UUID,
        *,
        expected_reason: str,
    ) -> TikTokPaidControlSnapshotView:
        require_paid_control_resume_scope(action_id)
        previous = self.get(action_id)
        if previous is None:
            raise ValueError("TikTok paid campaign has no control snapshot to resume")
        self._require_exact_customer_pause(previous, expected_reason)
        connection = self._connections.require_active(previous.product_id)
        access_token = self._secrets.resolve(connection.access_token_env)
        if access_token is None:
            raise ValueError(
                f"TikTok access-token secret {connection.access_token_env} is not available"
            )
        now = datetime.now(UTC)
        try:
            current = self._client.get_campaign_state(
                connection=connection,
                access_token=access_token,
                campaign_id=previous.campaign_id,
            )
            if current.operation_status.upper() != "DISABLE":
                return self._resume_unknown(
                    previous,
                    current,
                    "TikTok campaign drifted from the confirmed customer pause before resume",
                    now,
                )
            self._client.set_campaign_status(
                connection=connection,
                access_token=access_token,
                campaign_id=previous.campaign_id,
                operation_status="ENABLE",
            )
            verified = self._client.get_campaign_state(
                connection=connection,
                access_token=access_token,
                campaign_id=previous.campaign_id,
            )
        except TikTokMarketingApiError as exc:
            return self._resume_unknown(previous, None, str(exc)[:1000], now)
        if verified.operation_status.upper() != "ENABLE":
            return self._resume_unknown(
                previous,
                verified,
                "TikTok did not confirm operation_status=ENABLE after customer resume",
                now,
            )
        resumed = previous.model_copy(
            update={
                "operation_status": verified.operation_status,
                "primary_status": verified.primary_status,
                "secondary_status": verified.secondary_status,
                "pause_state": "NOT_REQUESTED",
                "pause_reason": None,
                "requires_reconciliation": False,
                "last_error": None,
                "synced_at": now,
                "paused_at": None,
            }
        )
        self._persist(resumed, spend_state="ACTIVE")
        return resumed

    def _require_exact_customer_pause(
        self,
        snapshot: TikTokPaidControlSnapshotView,
        expected_reason: str,
    ) -> None:
        if snapshot.pause_state != "CONFIRMED":
            raise ValueError("TikTok customer pause is not provider-confirmed")
        if snapshot.pause_reason != expected_reason:
            raise ValueError("TikTok campaign pause reason is not customer-resumeable")
        if snapshot.requires_reconciliation:
            raise ValueError("TikTok paid campaign requires reconciliation before resume")
        if snapshot.budget_guardrail_triggered:
            raise ValueError("TikTok budget guardrail pause cannot be customer-resumed")

    def _resume_unknown(
        self,
        previous: TikTokPaidControlSnapshotView,
        state: TikTokCampaignState | None,
        error: str,
        now: datetime,
    ) -> TikTokPaidControlSnapshotView:
        unknown = previous.model_copy(
            update={
                "operation_status": state.operation_status if state else previous.operation_status,
                "primary_status": state.primary_status if state else previous.primary_status,
                "secondary_status": state.secondary_status if state else previous.secondary_status,
                "pause_state": "UNKNOWN",
                "requires_reconciliation": True,
                "last_error": error[:1000],
                "synced_at": now,
            }
        )
        self._persist(unknown, spend_state="UNKNOWN")
        return unknown

    def _persist(self, snapshot: TikTokPaidControlSnapshotView, *, spend_state: str) -> None:
        self._store.put(
            TIKTOK_PAID_CONTROL_NAMESPACE,
            str(snapshot.action_id),
            snapshot.model_dump(mode="json"),
        )
        receipt = self._receipt(snapshot.action_id)
        metadata = dict(receipt.metadata)
        metadata["provider_control"] = {
            "operation_status": snapshot.operation_status,
            "primary_status": snapshot.primary_status,
            "secondary_status": snapshot.secondary_status,
            "provider_spend": snapshot.provider_spend,
            "synced_spend": snapshot.synced_spend,
            "currency": snapshot.currency,
            "pause_state": snapshot.pause_state,
            "pause_reason": snapshot.pause_reason,
            "synced_at": snapshot.synced_at.isoformat(),
        }
        metadata["spend_state"] = spend_state
        metadata["requires_reconciliation"] = snapshot.requires_reconciliation
        self._store.put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(snapshot.action_id),
            receipt.model_copy(update={"metadata": metadata}).model_dump(mode="json"),
        )

    def _receipt(self, action_id: UUID) -> ExecutionAdapterReceipt:
        payload = self._store.get(EXECUTION_ADAPTER_RECEIPT_NAMESPACE, str(action_id))
        if payload is None:
            raise ValueError("TikTok paid campaign has no execution receipt")
        return ExecutionAdapterReceipt.model_validate(payload)


class CustomerPaidCampaignLifecycleService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        *,
        controllers: dict[str, CustomerPaidProviderController] | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._controllers = controllers or {
            "meta-marketing-api": MetaCustomerPaidProviderController(self._store),
            "tiktok-marketing-api": TikTokCustomerPaidProviderController(self._store),
        }

    def pause_product(
        self,
        product_id: UUID,
        *,
        reason: str = AUTOPILOT_CUSTOMER_PAUSE_REASON,
    ) -> CustomerPaidCampaignLifecycleResult:
        candidates = self._candidates(product_id)
        customer_paused: list[UUID] = []
        preserved: list[UUID] = []
        reconciliation: list[UUID] = []
        mutations = 0
        for action, receipt in candidates:
            controller = self._controllers.get(receipt.provider)
            if controller is None:
                reconciliation.append(action.id)
                continue
            previous = controller.get(action.id)
            previous_reason = str(getattr(previous, "pause_reason", "") or "")
            previous_state = str(getattr(previous, "pause_state", "") or "")
            if previous_state == "CONFIRMED":
                if previous_reason == reason:
                    customer_paused.append(action.id)
                else:
                    preserved.append(action.id)
                continue
            pause_reason = previous_reason if previous_reason and previous_reason != reason else reason
            try:
                snapshot = controller.pause(action.id, reason=pause_reason)
                mutations += 1
            except (KeyError, RuntimeError, ValueError):
                reconciliation.append(action.id)
                continue
            if getattr(snapshot, "pause_state", None) != "CONFIRMED":
                reconciliation.append(action.id)
            elif getattr(snapshot, "pause_reason", None) == reason:
                customer_paused.append(action.id)
            else:
                preserved.append(action.id)
        return CustomerPaidCampaignLifecycleResult(
            product_id=product_id,
            candidate_count=len(candidates),
            provider_mutation_count=mutations,
            customer_paused_action_ids=customer_paused,
            preserved_pause_action_ids=preserved,
            reconciliation_action_ids=self._dedupe(reconciliation),
        )

    def resume_product(
        self,
        product_id: UUID,
        *,
        expected_reason: str = AUTOPILOT_CUSTOMER_PAUSE_REASON,
    ) -> CustomerPaidCampaignLifecycleResult:
        candidates = self._candidates(product_id)
        resumed: list[UUID] = []
        reconciliation: list[UUID] = []
        rollback_unknown: list[UUID] = []
        preserved: list[UUID] = []
        mutations = 0
        for action, receipt in candidates:
            controller = self._controllers.get(receipt.provider)
            if controller is None:
                reconciliation.append(action.id)
                continue
            snapshot = controller.get(action.id)
            if snapshot is None or getattr(snapshot, "pause_state", None) != "CONFIRMED":
                continue
            if getattr(snapshot, "pause_reason", None) != expected_reason:
                preserved.append(action.id)
                continue
            try:
                with paid_control_resume_scope(action.id):
                    updated = controller.resume_customer_pause(
                        action.id,
                        expected_reason=expected_reason,
                    )
                mutations += 1
            except (KeyError, RuntimeError, ValueError):
                reconciliation.append(action.id)
                break
            if (
                getattr(updated, "pause_state", None) != "NOT_REQUESTED"
                or bool(getattr(updated, "requires_reconciliation", False))
            ):
                reconciliation.append(action.id)
                break
            resumed.append(action.id)

        if reconciliation and resumed:
            for action_id in reversed(resumed):
                action, receipt = self._candidate_by_id(product_id, action_id)
                controller = self._controllers[receipt.provider]
                try:
                    rolled_back = controller.pause(action.id, reason=expected_reason)
                    mutations += 1
                except (KeyError, RuntimeError, ValueError):
                    rollback_unknown.append(action.id)
                    continue
                if getattr(rolled_back, "pause_state", None) != "CONFIRMED":
                    rollback_unknown.append(action.id)
            resumed = []

        return CustomerPaidCampaignLifecycleResult(
            product_id=product_id,
            candidate_count=len(candidates),
            provider_mutation_count=mutations,
            preserved_pause_action_ids=preserved,
            resumed_action_ids=resumed,
            reconciliation_action_ids=self._dedupe(reconciliation),
            rollback_unknown_action_ids=self._dedupe(rollback_unknown),
        )

    def repause_actions(
        self,
        product_id: UUID,
        action_ids: list[UUID],
        *,
        reason: str = AUTOPILOT_CUSTOMER_PAUSE_REASON,
    ) -> CustomerPaidCampaignLifecycleResult:
        reconciliation: list[UUID] = []
        confirmed: list[UUID] = []
        mutations = 0
        for action_id in action_ids:
            try:
                action, receipt = self._candidate_by_id(product_id, action_id)
                controller = self._controllers[receipt.provider]
                snapshot = controller.pause(action.id, reason=reason)
                mutations += 1
            except (KeyError, RuntimeError, ValueError):
                reconciliation.append(action_id)
                continue
            if getattr(snapshot, "pause_state", None) == "CONFIRMED":
                confirmed.append(action_id)
            else:
                reconciliation.append(action_id)
        return CustomerPaidCampaignLifecycleResult(
            product_id=product_id,
            candidate_count=len(action_ids),
            provider_mutation_count=mutations,
            customer_paused_action_ids=confirmed,
            reconciliation_action_ids=self._dedupe(reconciliation),
        )

    def _candidates(
        self,
        product_id: UUID,
    ) -> list[tuple[DistributionActionView, ExecutionAdapterReceipt]]:
        experiments: dict[UUID, DistributionExperimentView] = {}
        for payload in self._store.list_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE):
            experiment = DistributionExperimentView.model_validate(payload)
            if experiment.product_id == product_id:
                experiments[experiment.id] = experiment
        candidates: list[tuple[DistributionActionView, ExecutionAdapterReceipt]] = []
        for payload in self._store.list_namespace(DISTRIBUTION_ACTION_NAMESPACE):
            action = DistributionActionView.model_validate(payload)
            if action.action_type != DistributionActionType.PAID_CAMPAIGN:
                continue
            if action.status != DistributionActionStatus.EXECUTED:
                continue
            if action.experiment_id is None or action.experiment_id not in experiments:
                continue
            receipt_payload = self._store.get(
                EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
                str(action.id),
            )
            if receipt_payload is None:
                continue
            receipt = ExecutionAdapterReceipt.model_validate(receipt_payload)
            if receipt.outcome != AdapterExecutionOutcome.EXECUTED:
                continue
            if receipt.provider not in self._controllers:
                continue
            candidates.append((action, receipt))
        candidates.sort(key=lambda item: str(item[0].id))
        return candidates

    def _candidate_by_id(
        self,
        product_id: UUID,
        action_id: UUID,
    ) -> tuple[DistributionActionView, ExecutionAdapterReceipt]:
        for action, receipt in self._candidates(product_id):
            if action.id == action_id:
                return action, receipt
        raise KeyError(action_id)

    @staticmethod
    def _dedupe(values: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(values))


customer_paid_campaign_lifecycle_service = CustomerPaidCampaignLifecycleService()
