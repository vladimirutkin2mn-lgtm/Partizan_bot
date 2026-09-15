from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256
from uuid import UUID

from app.creative_assets import (
    CreativeAssetService,
    CreativeAssetStatus,
    CreativeReadinessStatus,
    creative_asset_service,
)
from app.customer_creative_binding import (
    CustomerCreativeBindingService,
    customer_creative_binding_service,
)
from app.customer_execution_boundary import customer_execution_request_scope
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
from app.distribution_types import DistributionActionStatus, DistributionActionType


class CustomerOperatorApprovalService:
    def __init__(
        self,
        *,
        request_service: CustomerExecutionRequestService | None = None,
        execution_service: InMemoryDistributionExecutionService | None = None,
        creative_service: CreativeAssetService | None = None,
        creative_binding_service: CustomerCreativeBindingService | None = None,
    ) -> None:
        self._request_service = request_service or customer_execution_request_service
        self._execution_service = execution_service or distribution_execution_service
        self._creative_service = creative_service or creative_asset_service
        self._creative_binding_service = (
            creative_binding_service or customer_creative_binding_service
        )

    def approve(self, request_id: UUID) -> CustomerExecutionRequestView:
        request = self._request_service.get_request(request_id)
        if request.status not in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"}:
            raise ValueError("Customer publish confirmation is required before operator approval.")
        if request.distribution_action_id is None or request.experiment_id is None:
            raise ValueError("Customer execution request is missing its prepared action or experiment.")

        plan = self._execution_service.get_plan(request.distribution_action_id)
        self.validate_exact_confirmation(request=request, plan=plan)

        if request.status == "OPERATOR_APPROVED":
            self._require_approved_pair(plan)
            return request

        action_status = plan.action.status
        experiment_status = plan.experiment.status
        if (
            action_status == DistributionActionStatus.PREPARED
            and experiment_status == DistributionExperimentStatus.DRAFT
        ):
            with customer_execution_request_scope(request.id):
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

    def validate_exact_confirmation(
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

        creative = self._validate_confirmed_creative(request=request, plan=plan)
        fingerprint = self._fingerprint(request=request, plan=plan, creative=creative)
        if fingerprint != request.customer_publish_confirmation_fingerprint:
            raise ValueError("Customer confirmation fingerprint no longer matches the exact action.")

    def _validate_confirmed_creative(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan: DistributionExecutionPlanView,
    ):
        if plan.action.action_type != DistributionActionType.ORGANIC_VIDEO:
            return None
        if (
            request.confirmed_creative_asset_id is None
            or request.confirmed_creative_asset_url is None
            or not request.confirmed_creative_brief_fingerprint
            or request.confirmed_creative_blob_id is None
            or not request.confirmed_creative_sha256
        ):
            raise ValueError("Customer-confirmed video binding is incomplete.")
        metadata = plan.action.operational_metadata
        if metadata.get("customer_confirmed_creative_asset_id") != str(
            request.confirmed_creative_asset_id
        ):
            raise ValueError("Customer-confirmed creative ID does not match the action stamp.")
        if metadata.get("customer_confirmed_creative_asset_url") != str(
            request.confirmed_creative_asset_url
        ):
            raise ValueError("Customer-confirmed creative URL does not match the action stamp.")
        if (
            metadata.get("customer_confirmed_creative_brief_fingerprint")
            != request.confirmed_creative_brief_fingerprint
        ):
            raise ValueError("Customer-confirmed creative brief does not match the action stamp.")
        if metadata.get("customer_confirmed_creative_blob_id") != str(
            request.confirmed_creative_blob_id
        ):
            raise ValueError("Customer-confirmed creative blob does not match the action stamp.")
        if metadata.get("customer_confirmed_creative_sha256") != request.confirmed_creative_sha256:
            raise ValueError("Customer-confirmed creative bytes do not match the action stamp.")
        try:
            asset = self._creative_service.get_asset(request.confirmed_creative_asset_id)
        except KeyError as exc:
            raise ValueError("Customer-confirmed video asset could not be found.") from exc
        if asset.action_id != plan.action.id or asset.status != CreativeAssetStatus.READY:
            raise ValueError("Customer-confirmed video is not the READY asset for this action.")
        if asset.public_url is None or str(asset.public_url) != str(request.confirmed_creative_asset_url):
            raise ValueError("Customer-confirmed video URL no longer matches the reviewed asset.")
        if asset.brief_fingerprint != request.confirmed_creative_brief_fingerprint:
            raise ValueError("Customer-confirmed video brief no longer matches the reviewed asset.")

        binding = self._creative_binding_service.validate_exact_video(asset)
        if binding.blob_id != request.confirmed_creative_blob_id:
            raise ValueError("Customer-confirmed video blob no longer matches the review.")
        if binding.sha256 != request.confirmed_creative_sha256:
            raise ValueError("Customer-confirmed video bytes no longer match the review.")

        readiness = self._creative_service.readiness(plan.action.id)
        if (
            readiness.status != CreativeReadinessStatus.READY
            or readiness.selected_asset is None
            or readiness.selected_asset.id != asset.id
        ):
            raise ValueError("Exact customer-confirmed video is no longer provider-ready.")
        return asset

    def _fingerprint(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan: DistributionExecutionPlanView,
        creative=None,
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
        if creative is not None:
            binding = self._creative_binding_service.validate_exact_video(creative)
            payload.update(
                {
                    "creative_asset_id": str(creative.id),
                    "creative_asset_url": str(creative.public_url),
                    "creative_brief_fingerprint": creative.brief_fingerprint,
                    "creative_blob_id": str(binding.blob_id),
                    "creative_sha256": binding.sha256,
                }
            )
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
