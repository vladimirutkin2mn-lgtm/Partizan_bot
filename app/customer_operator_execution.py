from __future__ import annotations

from uuid import UUID

from app.customer_execution_boundary import customer_execution_request_scope
from app.customer_execution_request_schemas import (
    CustomerExecutionRequestView,
    CustomerOperatorExecutionView,
)
from app.customer_execution_requests import (
    CustomerExecutionRequestService,
    customer_execution_request_service,
)
from app.customer_operator_approval import (
    CustomerOperatorApprovalService,
    customer_operator_approval_service,
)
from app.distribution_execution_schemas import (
    DistributionActionExecutionRequest,
    DistributionExecutionPlanView,
    DistributionExperimentStatus,
)
from app.distribution_execution_service import (
    InMemoryDistributionExecutionService,
    distribution_execution_service,
)
from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    ExecutionAdapterReceipt,
)
from app.organic_creative_execution import (
    organic_creative_distribution_execution_adapter_service,
)
from app.tiktok_direct_post_reconciliation import (
    TikTokDirectPostReconciliationService,
    TikTokDirectPostReconciliationStatus,
    TikTokDirectPostReconciliationView,
    tiktok_direct_post_reconciliation_service,
)
from app.tiktok_publish_authorization import (
    TikTokPublishAuthorizationService,
    tiktok_publish_authorization_service,
)


class CustomerOperatorExecutionService:
    def __init__(
        self,
        *,
        request_service: CustomerExecutionRequestService | None = None,
        execution_service: InMemoryDistributionExecutionService | None = None,
        approval_service: CustomerOperatorApprovalService | None = None,
        adapter_service: DistributionExecutionAdapterService | None = None,
        tiktok_reconciliation_service: TikTokDirectPostReconciliationService | None = None,
        tiktok_authorization_service: TikTokPublishAuthorizationService | None = None,
    ) -> None:
        self._request_service = request_service or customer_execution_request_service
        self._execution_service = execution_service or distribution_execution_service
        self._approval_service = approval_service or customer_operator_approval_service
        self._adapter_service = (
            adapter_service or organic_creative_distribution_execution_adapter_service
        )
        self._tiktok_reconciliation_service = (
            tiktok_reconciliation_service or tiktok_direct_post_reconciliation_service
        )
        self._tiktok_authorization_service = (
            tiktok_authorization_service or tiktok_publish_authorization_service
        )

    def view(self, request_id: UUID) -> CustomerOperatorExecutionView:
        request, plan = self._validated_request_plan(request_id)
        receipt = self._adapter_service.get_receipt(plan.action.id)
        self._require_execution_state(plan)
        plan, receipt = self._refresh_read_only_provider_reconciliation(
            request=request,
            plan=plan,
            receipt=receipt,
        )
        return self._to_view(request=request, plan=plan, receipt=receipt)

    def execute(self, request_id: UUID) -> CustomerOperatorExecutionView:
        request, plan = self._validated_request_plan(request_id)
        self._require_execution_state(plan)

        receipt = self._adapter_service.get_receipt(plan.action.id)
        if receipt is not None:
            # Customer-bound execution is deliberately one-shot. Any durable receipt means an
            # attempt already happened (or started), so this endpoint never mutates the provider
            # again. If an EXECUTED receipt was persisted before the local action transition,
            # reconcile only the local state without touching the provider.
            if (
                receipt.outcome == AdapterExecutionOutcome.EXECUTED
                and plan.action.status == DistributionActionStatus.APPROVED
                and plan.experiment.status == DistributionExperimentStatus.APPROVED
            ):
                with customer_execution_request_scope(request.id):
                    plan = self._execution_service.mark_executed(
                        plan.action.id,
                        DistributionActionExecutionRequest(
                            external_reference=receipt.external_reference,
                            executed_url=receipt.executed_url,
                            notes=(
                                f"Recovered from durable execution receipt {receipt.adapter_name} "
                                f"({receipt.provider}) without retrying the provider."
                            ),
                        ),
                    )
                self._approval_service.validate_exact_confirmation(
                    request=request,
                    plan=plan,
                )
            return self._to_view(request=request, plan=plan, receipt=receipt)

        if (
            plan.action.status != DistributionActionStatus.APPROVED
            or plan.experiment.status != DistributionExperimentStatus.APPROVED
        ):
            # Already-executed legacy/recovered state without a receipt is readable, but it must
            # never trigger another provider mutation.
            return self._to_view(request=request, plan=plan, receipt=None)

        self._validate_tiktok_authorization_if_usable(request=request, plan=plan)
        with customer_execution_request_scope(request.id):
            result = self._adapter_service.execute(
                plan.action.id,
                DistributionAdapterExecuteRequest(retry=False),
            )
        if result.receipt.action_id != plan.action.id:
            raise ValueError("Execution receipt does not match the customer-bound action.")
        if result.plan.action.id != plan.action.id or result.plan.experiment.id != plan.experiment.id:
            raise ValueError("Execution result does not match the customer-bound execution plan.")

        self._approval_service.validate_exact_confirmation(
            request=request,
            plan=result.plan,
        )
        self._require_execution_state(result.plan)
        return self._to_view(
            request=request,
            plan=result.plan,
            receipt=result.receipt,
        )

    def _validate_tiktok_authorization_if_usable(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan: DistributionExecutionPlanView,
    ) -> None:
        action = plan.action
        if (
            action.platform != DistributionPlatform.TIKTOK
            or action.action_type != DistributionActionType.ORGANIC_VIDEO
        ):
            return
        try:
            authorization = self._tiktok_authorization_service.get_current(
                action.id,
                require_usable=True,
            )
        except (KeyError, ValueError):
            # No usable authorization means the permissioned adapter can only return an assisted
            # preflight/consent state. It cannot submit a provider mutation.
            return
        if authorization.action_id != action.id:
            raise ValueError("TikTok publish authorization does not match the customer-bound action.")
        if (
            request.confirmed_creative_asset_id is None
            or authorization.creative_asset_id != request.confirmed_creative_asset_id
        ):
            raise ValueError(
                "TikTok publish authorization creative does not match the customer-confirmed video."
            )
        raw_title = action.content_payload.get("title")
        expected_title = raw_title if isinstance(raw_title, str) else ""
        if authorization.title != expected_title:
            raise ValueError(
                "TikTok publish authorization title does not match the customer-confirmed title."
            )

    def _refresh_read_only_provider_reconciliation(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan: DistributionExecutionPlanView,
        receipt: ExecutionAdapterReceipt | None,
    ) -> tuple[DistributionExecutionPlanView, ExecutionAdapterReceipt | None]:
        if not self._is_tiktok_reconcilable_receipt(receipt):
            return plan, receipt
        assert receipt is not None

        if plan.action.status == DistributionActionStatus.EXECUTED:
            try:
                latest = self._tiktok_reconciliation_service.get_latest(plan.action.id)
            except KeyError:
                return plan, receipt.model_copy(
                    update={
                        "outcome": AdapterExecutionOutcome.EXECUTED,
                        "message": (
                            "Customer-bound action is already EXECUTED locally; no provider retry "
                            "was attempted while reading execution state."
                        ),
                        "requires_operator_confirmation": False,
                    }
                )
            return plan, self._receipt_from_tiktok_reconciliation(receipt, latest)

        if (
            plan.action.status != DistributionActionStatus.APPROVED
            or plan.experiment.status != DistributionExperimentStatus.APPROVED
        ):
            return plan, receipt

        # TikTok Direct Post is asynchronous. Refreshing execution state may poll the provider's
        # read-only status endpoint, but it must never call Direct Post submission or retry an
        # existing publication. The Direct Post attempt is the durable recovery source when a
        # process crash left the generic adapter receipt incomplete. Only a confirmed
        # PUBLISH_COMPLETE may advance local state.
        try:
            with customer_execution_request_scope(request.id):
                reconciliation = self._tiktok_reconciliation_service.reconcile(
                    plan.action.id,
                    mark_executed=False,
                )
                if reconciliation.status == TikTokDirectPostReconciliationStatus.PUBLISHED:
                    note = "TikTok read-only reconciliation confirmed PUBLISH_COMPLETE"
                    if reconciliation.public_post_ids:
                        note += f"; public post ids: {','.join(reconciliation.public_post_ids)}"
                    plan = self._execution_service.mark_executed(
                        plan.action.id,
                        DistributionActionExecutionRequest(
                            external_reference=reconciliation.provider_publish_id,
                            notes=note,
                        ),
                    )
        except (KeyError, RuntimeError, ValueError):
            # Provider status polling is best-effort and never authorizes a retry. A transient
            # read failure leaves the original durable execution receipt untouched.
            return plan, receipt

        self._approval_service.validate_exact_confirmation(
            request=request,
            plan=plan,
        )
        return plan, self._receipt_from_tiktok_reconciliation(receipt, reconciliation)

    def _is_tiktok_reconcilable_receipt(
        self,
        receipt: ExecutionAdapterReceipt | None,
    ) -> bool:
        if receipt is None or receipt.provider != "tiktok-content-posting-api":
            return False
        return receipt.outcome in {
            AdapterExecutionOutcome.IN_PROGRESS,
            AdapterExecutionOutcome.ASSISTED,
        }

    def _receipt_from_tiktok_reconciliation(
        self,
        receipt: ExecutionAdapterReceipt,
        reconciliation: TikTokDirectPostReconciliationView,
    ) -> ExecutionAdapterReceipt:
        metadata = dict(receipt.metadata)
        metadata.update(
            {
                "provider_status": reconciliation.provider_status.value,
                "reconciliation_status": reconciliation.status.value,
                "public_post_ids": list(reconciliation.public_post_ids),
            }
        )
        if reconciliation.fail_reason:
            metadata["provider_fail_reason"] = reconciliation.fail_reason

        if reconciliation.status == TikTokDirectPostReconciliationStatus.PUBLISHED:
            outcome = AdapterExecutionOutcome.EXECUTED
            message = "TikTok provider confirmed PUBLISH_COMPLETE for the exact authorized video."
            requires_operator_confirmation = False
        elif reconciliation.status == TikTokDirectPostReconciliationStatus.FAILED:
            outcome = AdapterExecutionOutcome.FAILED
            message = (
                "TikTok provider confirmed that the publication failed. The existing customer-bound "
                "execution will not be retried automatically."
            )
            requires_operator_confirmation = True
        else:
            outcome = AdapterExecutionOutcome.IN_PROGRESS
            message = (
                "TikTok Direct Post is still processing. Partizan performed read-only provider "
                "reconciliation and did not submit or retry a publication."
            )
            requires_operator_confirmation = False

        return receipt.model_copy(
            update={
                "outcome": outcome,
                "message": message,
                "requires_operator_confirmation": requires_operator_confirmation,
                "external_reference": reconciliation.provider_publish_id,
                "metadata": metadata,
                "created_at": reconciliation.checked_at,
            }
        )

    def _validated_request_plan(
        self,
        request_id: UUID,
    ) -> tuple[CustomerExecutionRequestView, DistributionExecutionPlanView]:
        request = self._request_service.get_request(request_id)
        if request.status != "OPERATOR_APPROVED":
            raise ValueError("Operator approval is required before customer-bound execution.")
        if request.operator_approved_at is None:
            raise ValueError("Operator-approved request is missing its approval timestamp.")
        if request.distribution_action_id is None or request.experiment_id is None:
            raise ValueError("Customer execution request is missing its approved action or experiment.")

        plan = self._execution_service.get_plan(request.distribution_action_id)
        self._approval_service.validate_exact_confirmation(request=request, plan=plan)
        return request, plan

    def _require_execution_state(self, plan: DistributionExecutionPlanView) -> None:
        state = (plan.action.status, plan.experiment.status)
        if state not in {
            (DistributionActionStatus.APPROVED, DistributionExperimentStatus.APPROVED),
            (DistributionActionStatus.EXECUTED, DistributionExperimentStatus.RUNNING),
        }:
            raise ValueError(
                "Customer-bound execution requires APPROVED/APPROVED or EXECUTED/RUNNING state."
            )

    def _to_view(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan: DistributionExecutionPlanView,
        receipt: ExecutionAdapterReceipt | None,
    ) -> CustomerOperatorExecutionView:
        return CustomerOperatorExecutionView(
            request_id=request.id,
            distribution_action_id=plan.action.id,
            action_status=plan.action.status,
            experiment_status=plan.experiment.status,
            receipt=receipt,
            retry_allowed=False,
        )


customer_operator_execution_service = CustomerOperatorExecutionService()
