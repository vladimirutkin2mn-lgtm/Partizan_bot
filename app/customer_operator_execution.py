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
        tiktok_authorization_service: TikTokPublishAuthorizationService | None = None,
    ) -> None:
        self._request_service = request_service or customer_execution_request_service
        self._execution_service = execution_service or distribution_execution_service
        self._approval_service = approval_service or customer_operator_approval_service
        self._adapter_service = (
            adapter_service or organic_creative_distribution_execution_adapter_service
        )
        self._tiktok_authorization_service = (
            tiktok_authorization_service or tiktok_publish_authorization_service
        )

    def view(self, request_id: UUID) -> CustomerOperatorExecutionView:
        request, plan = self._validated_request_plan(request_id)
        receipt = self._adapter_service.get_receipt(plan.action.id)
        self._require_execution_state(plan)
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
