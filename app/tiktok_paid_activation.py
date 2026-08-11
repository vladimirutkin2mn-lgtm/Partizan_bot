from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionStatus, DistributionPlatform
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    DistributionAdapterExecutionView,
    EnvironmentSecretResolver,
    ExecutionAdapterReceipt,
    SecretResolver,
    distribution_execution_adapter_service,
)
from app.paid_campaign import PaidCampaignSpecService, paid_campaign_spec_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.tiktok_marketing_api import (
    HttpxTikTokMarketingApiClient,
    TikTokMarketingApiClient,
    TikTokMarketingApiError,
)
from app.tiktok_paid_provider import (
    TikTokPaidProviderConnectionService,
    tiktok_paid_provider_connection_service,
)

TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE = "tiktok_paid_activation_authorization"


class TikTokPaidActivationAuthorizationRequest(BaseModel):
    approved_budget_cap: float = Field(gt=0)
    confirm_spend: bool


class TikTokPaidActivationAuthorizationView(BaseModel):
    id: UUID
    action_id: UUID
    product_id: UUID
    provider: str = "tiktok-marketing-api"
    approved_budget_cap: float = Field(gt=0)
    created_at: datetime
    expires_at: datetime
    attempted_at: datetime | None = None
    consumed_at: datetime | None = None


class TikTokPaidActivationRequest(BaseModel):
    authorization_id: UUID


class TikTokPaidActivationService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        client: TikTokMarketingApiClient | None = None,
        secret_resolver: SecretResolver | None = None,
        connection_service: TikTokPaidProviderConnectionService | None = None,
        spec_service: PaidCampaignSpecService | None = None,
        authorization_ttl_minutes: int = 15,
    ) -> None:
        self._store = store or get_runtime_store()
        self._client = client or HttpxTikTokMarketingApiClient()
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._connection_service = connection_service or tiktok_paid_provider_connection_service
        self._spec_service = spec_service or paid_campaign_spec_service
        self._authorization_ttl = timedelta(minutes=authorization_ttl_minutes)

    def authorize(
        self,
        action_id: UUID,
        payload: TikTokPaidActivationAuthorizationRequest,
    ) -> TikTokPaidActivationAuthorizationView:
        if not payload.confirm_spend:
            raise ValueError("confirm_spend=true is required to authorize paid activation")
        action = distribution_execution_service.get_action(action_id)
        if action.status != DistributionActionStatus.APPROVED:
            raise ValueError("Only APPROVED paid actions can receive activation authorization")
        if action.platform != DistributionPlatform.TIKTOK:
            raise ValueError("This activation boundary supports TikTok paid actions only")
        receipt = self._require_staged_receipt(action_id)
        self._require_provider_ids(receipt)
        spec = self._spec_service.get(action_id)
        if spec is None:
            raise ValueError("PaidCampaignSpec is required before activation authorization")
        if round(payload.approved_budget_cap, 2) != round(spec.budget_cap, 2):
            raise ValueError(
                "approved_budget_cap must exactly match the staged PaidCampaignSpec budget cap"
            )
        if action.experiment_id is None:
            raise ValueError("Paid action has no DistributionExperiment")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        now = datetime.now(UTC)
        authorization = TikTokPaidActivationAuthorizationView(
            id=uuid4(),
            action_id=action.id,
            product_id=experiment.product_id,
            approved_budget_cap=round(spec.budget_cap, 2),
            created_at=now,
            expires_at=now + self._authorization_ttl,
        )
        self._persist_authorization(authorization)
        return authorization

    def activate(
        self,
        action_id: UUID,
        payload: TikTokPaidActivationRequest,
    ) -> DistributionAdapterExecutionView:
        action = distribution_execution_service.get_action(action_id)
        if action.status != DistributionActionStatus.APPROVED:
            raise ValueError("Only APPROVED paid actions can be activated")
        authorization = self._get_authorization(payload.authorization_id)
        if authorization.action_id != action_id:
            raise ValueError("Activation authorization belongs to a different action")
        now = datetime.now(UTC)
        if authorization.attempted_at is not None:
            raise ValueError("Activation authorization has already been attempted")
        if authorization.consumed_at is not None:
            raise ValueError("Activation authorization has already been consumed")
        if authorization.expires_at <= now:
            raise ValueError("Activation authorization has expired")

        receipt = self._require_staged_receipt(action_id)
        provider_ids = self._require_provider_ids(receipt)
        spec = self._spec_service.get(action_id)
        if spec is None:
            raise ValueError("PaidCampaignSpec is required before activation")
        if round(authorization.approved_budget_cap, 2) != round(spec.budget_cap, 2):
            raise ValueError("Activation authorization budget no longer matches PaidCampaignSpec")
        connection = self._connection_service.require_active(authorization.product_id)
        access_token = self._secret_resolver.resolve(connection.access_token_env)
        if access_token is None:
            raise ValueError(
                f"TikTok access-token secret {connection.access_token_env} is not available"
            )

        attempted = authorization.model_copy(update={"attempted_at": now})
        self._persist_authorization(attempted)
        activated_steps: list[str] = []
        try:
            self._client.set_ad_status(
                connection=connection,
                access_token=access_token,
                ad_id=provider_ids["ad_id"],
                operation_status="ENABLE",
            )
            activated_steps.append("ad")
            self._client.set_adgroup_status(
                connection=connection,
                access_token=access_token,
                adgroup_id=provider_ids["adgroup_id"],
                operation_status="ENABLE",
            )
            activated_steps.append("adgroup")
            self._client.set_campaign_status(
                connection=connection,
                access_token=access_token,
                campaign_id=provider_ids["campaign_id"],
                operation_status="ENABLE",
            )
            activated_steps.append("campaign")
        except TikTokMarketingApiError as exc:
            campaign_attempted = len(activated_steps) >= 2
            metadata = dict(receipt.metadata)
            metadata.update(
                {
                    "activation_failed_at": datetime.now(UTC).isoformat(),
                    "activation_error": str(exc)[:1000],
                    "activation_steps_completed": activated_steps,
                    "activation_authorization_id": str(authorization.id),
                    "spend_state": "UNKNOWN" if campaign_attempted else "NOT_STARTED",
                    "requires_reconciliation": True,
                }
            )
            failed_staged = receipt.model_copy(
                update={
                    "message": (
                        "TikTok activation attempt did not complete. Provider state must be "
                        "reconciled before issuing a new authorization."
                    ),
                    "requires_operator_confirmation": True,
                    "metadata": metadata,
                }
            )
            self._persist_receipt(failed_staged)
            return DistributionAdapterExecutionView(
                receipt=failed_staged,
                plan=distribution_execution_service.get_plan(action_id),
            )

        activated_at = datetime.now(UTC)
        consumed = attempted.model_copy(update={"consumed_at": activated_at})
        self._persist_authorization(consumed)
        metadata = dict(receipt.metadata)
        metadata.update(
            {
                "activated_at": activated_at.isoformat(),
                "activation_steps_completed": activated_steps,
                "activation_authorization_id": str(authorization.id),
                "spend_started": True,
                "spend_state": "ACTIVE",
            }
        )
        executed_receipt = receipt.model_copy(
            update={
                "outcome": AdapterExecutionOutcome.EXECUTED,
                "message": (
                    "TikTok ad, ad group and campaign were explicitly enabled after one-time "
                    "budget authorization."
                ),
                "requires_operator_confirmation": False,
                "metadata": metadata,
                "created_at": activated_at,
            }
        )
        plan = distribution_execution_service.mark_executed(
            action_id,
            DistributionActionExecutionRequest(
                external_reference=executed_receipt.external_reference,
                notes=(
                    "TikTok paid campaign activated using explicit authorization "
                    f"{authorization.id} for budget cap {authorization.approved_budget_cap:.2f}."
                ),
            ),
        )
        self._persist_receipt(executed_receipt)
        return DistributionAdapterExecutionView(receipt=executed_receipt, plan=plan)

    def get_authorization(
        self,
        authorization_id: UUID,
    ) -> TikTokPaidActivationAuthorizationView:
        return self._get_authorization(authorization_id)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE)

    def _require_staged_receipt(self, action_id: UUID) -> ExecutionAdapterReceipt:
        receipt = distribution_execution_adapter_service.get_receipt(action_id)
        if receipt is None:
            raise ValueError("Stage the TikTok campaign before requesting activation")
        if receipt.provider != "tiktok-marketing-api":
            raise ValueError("Activation requires a TikTok Marketing API execution receipt")
        if receipt.outcome != AdapterExecutionOutcome.STAGED:
            raise ValueError("Activation requires a STAGED TikTok execution receipt")
        if receipt.metadata.get("requires_reconciliation"):
            raise ValueError("Reconcile the previous TikTok activation attempt before proceeding")
        return receipt

    def _require_provider_ids(self, receipt: ExecutionAdapterReceipt) -> dict[str, str]:
        raw = receipt.metadata.get("provider_ids")
        if not isinstance(raw, dict):
            raise ValueError("STAGED TikTok receipt has no provider IDs")
        required = ("campaign_id", "adgroup_id", "ad_id")
        if any(not raw.get(key) for key in required):
            raise ValueError("STAGED TikTok receipt has incomplete provider IDs")
        return {key: str(raw[key]) for key in required}

    def _get_authorization(
        self,
        authorization_id: UUID,
    ) -> TikTokPaidActivationAuthorizationView:
        payload = self._store.get(
            TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
            str(authorization_id),
        )
        if payload is None:
            raise KeyError(authorization_id)
        return TikTokPaidActivationAuthorizationView.model_validate(payload)

    def _persist_authorization(
        self,
        authorization: TikTokPaidActivationAuthorizationView,
    ) -> None:
        self._store.put(
            TIKTOK_PAID_ACTIVATION_AUTHORIZATION_NAMESPACE,
            str(authorization.id),
            authorization.model_dump(mode="json"),
        )

    def _persist_receipt(self, receipt: ExecutionAdapterReceipt) -> None:
        self._store.put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(receipt.action_id),
            receipt.model_dump(mode="json"),
        )


tiktok_paid_activation_service = TikTokPaidActivationService()
