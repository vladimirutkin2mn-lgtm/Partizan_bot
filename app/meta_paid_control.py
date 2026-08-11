from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, Field

from app.distribution_analytics_schemas import DistributionSpendCreate
from app.distribution_analytics_service import (
    InMemoryDistributionAnalyticsService,
    distribution_analytics_service,
)
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    EnvironmentSecretResolver,
    ExecutionAdapterReceipt,
    SecretResolver,
    distribution_execution_adapter_service,
)
from app.meta_marketing_api import (
    HttpxMetaMarketingApiClient,
    MetaCampaignInsights,
    MetaCampaignState,
    MetaMarketingApiClient,
    MetaMarketingApiError,
)
from app.paid_campaign import PaidCampaignSpec, PaidCampaignSpecService, paid_campaign_spec_service
from app.paid_provider_connections import (
    PaidProviderConnectionService,
    PaidProviderConnectionView,
    paid_provider_connection_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

META_PAID_CONTROL_NAMESPACE = "meta_paid_control_snapshot"


class MetaPaidControlSnapshotView(BaseModel):
    action_id: UUID
    experiment_id: UUID
    product_id: UUID
    campaign_id: str
    configured_status: str
    effective_status: str
    provider_spend: float = Field(ge=0)
    synced_spend: float = Field(ge=0)
    last_spend_delta: float = Field(ge=0)
    impressions: int = Field(ge=0)
    clicks: int = Field(ge=0)
    account_currency: str | None = None
    budget_cap: float = Field(gt=0)
    sync_state: Literal["SYNCED", "UNKNOWN"]
    budget_guardrail_triggered: bool = False
    pause_state: Literal["NOT_REQUESTED", "CONFIRMED", "UNKNOWN"] = "NOT_REQUESTED"
    pause_reason: str | None = None
    requires_reconciliation: bool = False
    last_error: str | None = None
    synced_at: datetime
    paused_at: datetime | None = None


class MetaPaidControlService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        meta_client: MetaMarketingApiClient | None = None,
        secret_resolver: SecretResolver | None = None,
        connection_service: PaidProviderConnectionService | None = None,
        spec_service: PaidCampaignSpecService | None = None,
        analytics_service: InMemoryDistributionAnalyticsService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._meta_client = meta_client or HttpxMetaMarketingApiClient()
        self._secret_resolver = secret_resolver or EnvironmentSecretResolver()
        self._connection_service = connection_service or paid_provider_connection_service
        self._spec_service = spec_service or paid_campaign_spec_service
        self._analytics_service = analytics_service or distribution_analytics_service

    def sync(self, action_id: UUID) -> MetaPaidControlSnapshotView:
        context = self._context(action_id)
        previous = self.get(action_id)
        now = datetime.now(UTC)
        try:
            state = self._meta_client.get_campaign_state(
                connection=context.connection,
                access_token=context.access_token,
                campaign_id=context.campaign_id,
            )
            insights = self._meta_client.get_campaign_insights(
                connection=context.connection,
                access_token=context.access_token,
                campaign_id=context.campaign_id,
            )
        except MetaMarketingApiError as exc:
            snapshot = self._unknown_snapshot(context, previous, str(exc), now)
            self._persist(snapshot)
            return snapshot

        provider_spend = round(max(0.0, insights.spend), 2)
        prior_synced = previous.synced_spend if previous is not None else 0.0
        spend_regressed = provider_spend + 0.005 < prior_synced
        delta = round(max(0.0, provider_spend - prior_synced), 2)
        synced_spend = prior_synced
        last_error: str | None = None
        requires_reconciliation = False

        if delta > 0 and context.action_status == DistributionActionStatus.EXECUTED:
            self._ingest_spend_delta(context, provider_spend, delta, insights)
            synced_spend = round(prior_synced + delta, 2)
        elif delta > 0:
            last_error = "Meta reported spend before the local paid experiment was RUNNING"
            requires_reconciliation = True

        if spend_regressed:
            last_error = "Meta cumulative spend is lower than the amount already synced locally"
            requires_reconciliation = True

        budget_guardrail = provider_spend + 0.005 >= context.spec.budget_cap
        unexpected_active = (
            context.action_status != DistributionActionStatus.EXECUTED
            and state.configured_status.upper() != "PAUSED"
        )
        receipt_reconciliation = bool(context.receipt.metadata.get("requires_reconciliation"))
        pause_reason: str | None = None
        if budget_guardrail:
            pause_reason = "BUDGET_CAP"
        elif unexpected_active or receipt_reconciliation:
            pause_reason = "RECONCILIATION"

        pause_state: Literal["NOT_REQUESTED", "CONFIRMED", "UNKNOWN"] = "NOT_REQUESTED"
        paused_at = previous.paused_at if previous is not None else None
        if state.configured_status.upper() == "PAUSED":
            pause_state = "CONFIRMED"
            paused_at = paused_at or now
            if receipt_reconciliation:
                requires_reconciliation = False
        elif pause_reason is not None:
            state, pause_state, pause_error = self._pause_and_verify(context, state)
            if pause_state == "CONFIRMED":
                paused_at = now
                if pause_reason == "RECONCILIATION":
                    requires_reconciliation = False
            else:
                requires_reconciliation = True
                last_error = pause_error or last_error

        snapshot = MetaPaidControlSnapshotView(
            action_id=context.action_id,
            experiment_id=context.experiment_id,
            product_id=context.product_id,
            campaign_id=context.campaign_id,
            configured_status=state.configured_status,
            effective_status=state.effective_status,
            provider_spend=provider_spend,
            synced_spend=synced_spend,
            last_spend_delta=delta if synced_spend > prior_synced else 0.0,
            impressions=insights.impressions,
            clicks=insights.clicks,
            account_currency=insights.account_currency,
            budget_cap=context.spec.budget_cap,
            sync_state="SYNCED",
            budget_guardrail_triggered=budget_guardrail,
            pause_state=pause_state,
            pause_reason=pause_reason,
            requires_reconciliation=requires_reconciliation,
            last_error=last_error,
            synced_at=now,
            paused_at=paused_at,
        )
        self._persist(snapshot)
        self._update_receipt_control(context.receipt, snapshot)
        return snapshot

    def pause(self, action_id: UUID, *, reason: str = "EMERGENCY") -> MetaPaidControlSnapshotView:
        context = self._context(action_id)
        previous = self.get(action_id)
        now = datetime.now(UTC)
        try:
            state = self._meta_client.get_campaign_state(
                connection=context.connection,
                access_token=context.access_token,
                campaign_id=context.campaign_id,
            )
            if state.configured_status.upper() != "PAUSED":
                state, pause_state, pause_error = self._pause_and_verify(context, state)
            else:
                pause_state, pause_error = "CONFIRMED", None
        except MetaMarketingApiError as exc:
            state = MetaCampaignState(
                campaign_id=context.campaign_id,
                configured_status="UNKNOWN",
                effective_status="UNKNOWN",
            )
            pause_state, pause_error = "UNKNOWN", str(exc)

        snapshot = MetaPaidControlSnapshotView(
            action_id=context.action_id,
            experiment_id=context.experiment_id,
            product_id=context.product_id,
            campaign_id=context.campaign_id,
            configured_status=state.configured_status,
            effective_status=state.effective_status,
            provider_spend=previous.provider_spend if previous else 0.0,
            synced_spend=previous.synced_spend if previous else 0.0,
            last_spend_delta=0.0,
            impressions=previous.impressions if previous else 0,
            clicks=previous.clicks if previous else 0,
            account_currency=previous.account_currency if previous else None,
            budget_cap=context.spec.budget_cap,
            sync_state="SYNCED" if state.configured_status != "UNKNOWN" else "UNKNOWN",
            budget_guardrail_triggered=(
                previous.budget_guardrail_triggered if previous else False
            ),
            pause_state=pause_state,
            pause_reason=reason[:120],
            requires_reconciliation=pause_state != "CONFIRMED",
            last_error=pause_error,
            synced_at=now,
            paused_at=now if pause_state == "CONFIRMED" else None,
        )
        self._persist(snapshot)
        self._update_receipt_control(context.receipt, snapshot)
        return snapshot

    def get(self, action_id: UUID) -> MetaPaidControlSnapshotView | None:
        payload = self._store.get(META_PAID_CONTROL_NAMESPACE, str(action_id))
        if payload is None:
            return None
        return MetaPaidControlSnapshotView.model_validate(payload)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(META_PAID_CONTROL_NAMESPACE)

    def _context(self, action_id: UUID) -> _MetaControlContext:
        action = distribution_execution_service.get_action(action_id)
        if (
            action.platform != DistributionPlatform.INSTAGRAM
            or action.action_type != DistributionActionType.PAID_CAMPAIGN
        ):
            raise ValueError("Meta paid control supports Instagram PAID_CAMPAIGN actions only")
        if action.status not in {
            DistributionActionStatus.APPROVED,
            DistributionActionStatus.EXECUTED,
        }:
            raise ValueError("Meta paid control requires an APPROVED or EXECUTED action")
        if action.experiment_id is None:
            raise ValueError("Meta paid action has no DistributionExperiment")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        receipt = distribution_execution_adapter_service.get_receipt(action_id)
        if receipt is None or receipt.provider != "meta-marketing-api":
            raise ValueError("Meta paid control requires a Meta execution receipt")
        if receipt.outcome not in {
            AdapterExecutionOutcome.STAGED,
            AdapterExecutionOutcome.EXECUTED,
        }:
            raise ValueError("Meta paid control requires a STAGED or EXECUTED receipt")
        provider_ids = receipt.metadata.get("provider_ids")
        if not isinstance(provider_ids, dict) or not provider_ids.get("campaign_id"):
            raise ValueError("Meta execution receipt has no campaign_id")
        spec = self._spec_service.get(action_id)
        if spec is None:
            raise ValueError("PaidCampaignSpec is required for Meta paid control")
        connection = self._connection_service.require_active_meta(experiment.product_id)
        access_token = self._secret_resolver.resolve(connection.access_token_env)
        if access_token is None:
            raise ValueError(
                f"Meta access-token secret {connection.access_token_env} is not available"
            )
        return _MetaControlContext(
            action_id=action.id,
            action_status=action.status,
            experiment_id=experiment.id,
            product_id=experiment.product_id,
            campaign_id=str(provider_ids["campaign_id"]),
            receipt=receipt,
            spec=spec,
            connection=connection,
            access_token=access_token,
        )

    def _ingest_spend_delta(
        self,
        context: _MetaControlContext,
        provider_spend: float,
        delta: float,
        insights: MetaCampaignInsights,
    ) -> None:
        spend_id = uuid5(
            NAMESPACE_URL,
            f"meta:{context.campaign_id}:cumulative:{provider_spend:.2f}",
        )
        self._analytics_service.add_spend(
            context.experiment_id,
            DistributionSpendCreate(
                spend_id=spend_id,
                amount=delta,
                occurred_at=datetime.now(UTC),
                properties={
                    "source": "META_MARKETING_API",
                    "campaign_id": context.campaign_id,
                    "provider_cumulative_spend": provider_spend,
                    "account_currency": insights.account_currency,
                    "impressions": insights.impressions,
                    "clicks": insights.clicks,
                },
            ),
        )

    def _pause_and_verify(
        self,
        context: _MetaControlContext,
        current: MetaCampaignState,
    ) -> tuple[MetaCampaignState, Literal["CONFIRMED", "UNKNOWN"], str | None]:
        try:
            self._meta_client.set_status(
                connection=context.connection,
                access_token=context.access_token,
                object_id=context.campaign_id,
                status="PAUSED",
            )
            verified = self._meta_client.get_campaign_state(
                connection=context.connection,
                access_token=context.access_token,
                campaign_id=context.campaign_id,
            )
        except MetaMarketingApiError as exc:
            return current, "UNKNOWN", str(exc)[:1000]
        if verified.configured_status.upper() != "PAUSED":
            return verified, "UNKNOWN", "Meta did not confirm configured_status=PAUSED"
        return verified, "CONFIRMED", None

    def _unknown_snapshot(
        self,
        context: _MetaControlContext,
        previous: MetaPaidControlSnapshotView | None,
        error: str,
        now: datetime,
    ) -> MetaPaidControlSnapshotView:
        return MetaPaidControlSnapshotView(
            action_id=context.action_id,
            experiment_id=context.experiment_id,
            product_id=context.product_id,
            campaign_id=context.campaign_id,
            configured_status=previous.configured_status if previous else "UNKNOWN",
            effective_status=previous.effective_status if previous else "UNKNOWN",
            provider_spend=previous.provider_spend if previous else 0.0,
            synced_spend=previous.synced_spend if previous else 0.0,
            last_spend_delta=0.0,
            impressions=previous.impressions if previous else 0,
            clicks=previous.clicks if previous else 0,
            account_currency=previous.account_currency if previous else None,
            budget_cap=context.spec.budget_cap,
            sync_state="UNKNOWN",
            budget_guardrail_triggered=(
                previous.budget_guardrail_triggered if previous else False
            ),
            pause_state=previous.pause_state if previous else "NOT_REQUESTED",
            pause_reason=previous.pause_reason if previous else None,
            requires_reconciliation=previous.requires_reconciliation if previous else False,
            last_error=error[:1000],
            synced_at=now,
            paused_at=previous.paused_at if previous else None,
        )

    def _persist(self, snapshot: MetaPaidControlSnapshotView) -> None:
        self._store.put(
            META_PAID_CONTROL_NAMESPACE,
            str(snapshot.action_id),
            snapshot.model_dump(mode="json"),
        )

    def _update_receipt_control(
        self,
        receipt: ExecutionAdapterReceipt,
        snapshot: MetaPaidControlSnapshotView,
    ) -> None:
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
        if snapshot.pause_state == "CONFIRMED":
            metadata["spend_state"] = "PAUSED"
        metadata["requires_reconciliation"] = snapshot.requires_reconciliation
        updated = receipt.model_copy(update={"metadata": metadata})
        self._store.put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(receipt.action_id),
            updated.model_dump(mode="json"),
        )


class _MetaControlContext:
    def __init__(
        self,
        *,
        action_id: UUID,
        action_status: DistributionActionStatus,
        experiment_id: UUID,
        product_id: UUID,
        campaign_id: str,
        receipt: ExecutionAdapterReceipt,
        spec: PaidCampaignSpec,
        connection: PaidProviderConnectionView,
        access_token: str,
    ) -> None:
        self.action_id = action_id
        self.action_status = action_status
        self.experiment_id = experiment_id
        self.product_id = product_id
        self.campaign_id = campaign_id
        self.receipt = receipt
        self.spec = spec
        self.connection = connection
        self.access_token = access_token


meta_paid_control_service = MetaPaidControlService()
