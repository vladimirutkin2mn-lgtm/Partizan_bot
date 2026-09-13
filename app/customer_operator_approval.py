from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from app.customer_execution_request_schemas import CustomerExecutionRequestView
from app.customer_execution_requests import (
    CustomerExecutionRequestService,
    customer_execution_request_service,
)
from app.distribution_execution_schemas import (
    DistributionExecutionPlanView,
    DistributionExperimentStatus,
)
from app.distribution_execution_service import (
    InMemoryDistributionExecutionService,
    distribution_execution_service,
)
from app.distribution_types import DistributionActionStatus


class CustomerOperatorApprovalService:
    def __init__(
        self,
        *,
        request_service: CustomerExecutionRequestService | None = None,
        execution_service: InMemoryDistributionExecutionService | None = None,
    ) -> None:
        self._request_service = request_service or customer_execution_request_service
        self._execution_service = execution_service or distribution_execution_service

    def approve(self, request_id: UUID) -> CustomerExecutionRequestView:
        request = self._request_service.get_request(request_id)
        if request.status not in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"}:
            raise ValueError("Customer publish confirmation is required before operator approval.")
        if request.distribution_action_id is None or request.experiment_id is None:
            raise ValueError("Customer execution request is missing its prepared action or experiment.")

        plan = self._execution_service.get_plan(request.distribution_action_id)
        self._validate_exact_confirmation(request=request, plan=plan)

        if request.status == "OPERATOR_APPROVED":
            self._require_approved_pair(plan)
            return request

        action_status = plan.action.status
        experiment_status = plan.experiment.status
        if (
            action_status == DistributionActionStatus.PREPARED
            and experiment_status == DistributionExperimentStatus.DRAFT
        ):
            approved_plan = self._execution_service.approve(plan.action.id)
        elif (
            action_status == DistributionActionStatus.APPROVED
            and experiment_status == DistributionExperimentStatus.APPROVED
        ):
            # Recover safely if the action approval write succeeded but the request marker did not.
            approved_plan = plan
        else:
            raise ValueError(
                "Customer execution approval requires a PREPARED/DRAFT or APPROVED/APPROVED plan."
            )

        self._require_approved_pair(approved_plan)
        return self._request_service.mark_operator_approved(
            request_id=request.id,
            plan=approved_plan,
        )

    def _validate_exact_confirmation(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan: DistributionExecutionPlanView,
    ) -> None:
        action = plan.action
        experiment = plan.experiment
        if request.distribution_action_id != action.id:
            raise ValueError("Prepared action does not match the customer execution request.")
        if request.experiment_id != experiment.id or action.experiment_id != experiment.id:
            raise ValueError("Prepared action and experiment do not match the customer execution request.")
        if request.distribution_play_id != experiment.distribution_play_id:
            raise ValueError("Prepared action does not belong to the linked DistributionPlay.")
        if request.opportunity_id != experiment.opportunity_id:
            raise ValueError("Prepared action does not belong to the linked opportunity.")
        if action.opportunity_id != request.opportunity_id:
            raise ValueError("Prepared action opportunity does not match the customer request.")
        if action.platform != request.platform:
            raise ValueError("Prepared action platform does not match the customer request.")
        if request.context_text is None:
            raise ValueError("Customer execution request is missing the exact accepted context.")
        if action.content_text != request.content_text:
            raise ValueError("Prepared action content no longer matches the accepted customer draft.")
        if action.content_payload.get("context_text") != request.context_text:
            raise ValueError("Prepared action context no longer matches the accepted customer draft.")
        expected_title = request.draft_title.strip() if request.draft_title else None
        if action.content_payload.get("title") != expected_title:
            raise ValueError("Prepared action title no longer matches the accepted customer draft.")
        if str(action.target_url or "") != str(request.source_url):
            raise ValueError("Prepared action target no longer matches the customer research source.")

        metadata = action.operational_metadata
        if metadata.get("customer_execution_request_id") != str(request.id):
            raise ValueError("Prepared action is not bound to this customer execution request.")
        if metadata.get("customer_exact_content_locked") is not True:
            raise ValueError("Prepared customer action must keep exact accepted content locked.")
        if metadata.get("customer_publish_confirmation_required") is not True:
            raise ValueError("Prepared customer action must require final customer confirmation.")
        if request.customer_publish_confirmed_at is None:
            raise ValueError("Customer confirmation record is missing its timestamp.")
        if not request.customer_publish_confirmation_fingerprint:
            raise ValueError("Customer confirmation record is missing its fingerprint.")

        metadata_confirmed_at = metadata.get("customer_publish_confirmed_at")
        metadata_fingerprint = metadata.get("customer_publish_confirmation_fingerprint")
        if not metadata_confirmed_at or not metadata_fingerprint:
            raise ValueError("Prepared action is missing its durable customer confirmation stamp.")
        try:
            parsed_confirmed_at = datetime.fromisoformat(str(metadata_confirmed_at))
        except ValueError as exc:
            raise ValueError("Prepared action contains an invalid customer confirmation timestamp.") from exc
        if parsed_confirmed_at != request.customer_publish_confirmed_at:
            raise ValueError("Customer confirmation timestamps do not match.")
        if metadata_fingerprint != request.customer_publish_confirmation_fingerprint:
            raise ValueError("Customer confirmation fingerprints do not match.")

        fingerprint = self._fingerprint(request=request, plan=plan)
        if fingerprint != request.customer_publish_confirmation_fingerprint:
            raise ValueError("Customer confirmation fingerprint no longer matches the exact action.")

    def _fingerprint(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan: DistributionExecutionPlanView,
    ) -> str:
        payload = {
            "request_id": str(request.id),
            "action_id": str(plan.action.id),
            "platform": request.platform.value,
            "target_url": str(plan.action.target_url),
            "title": plan.action.content_payload.get("title"),
            "context_text": plan.action.content_payload.get("context_text"),
            "content_text": plan.action.content_text,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()

    def _require_approved_pair(self, plan: DistributionExecutionPlanView) -> None:
        if plan.action.status != DistributionActionStatus.APPROVED:
            raise ValueError("Customer execution action was not approved.")
        if plan.experiment.status != DistributionExperimentStatus.APPROVED:
            raise ValueError("Customer execution experiment was not approved.")


customer_operator_approval_service = CustomerOperatorApprovalService()
