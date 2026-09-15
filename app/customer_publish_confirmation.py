from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from app.creative_assets import (
    CreativeAssetService,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeReadinessStatus,
    creative_asset_service,
)
from app.customer_channel_schemas import CustomerStartingMoveDraftView
from app.customer_creative_binding import (
    CustomerCreativeBindingService,
    customer_creative_binding_service,
)
from app.customer_execution_request_schemas import (
    CustomerExecutionRequestView,
    CustomerPreparedActionView,
)
from app.customer_execution_requests import (
    CUSTOMER_EXECUTION_REQUEST_NAMESPACE,
    CustomerExecutionRequestService,
    customer_execution_request_service,
)
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    InMemoryDistributionExecutionService,
    distribution_execution_service,
)
from app.distribution_types import DistributionActionStatus, DistributionActionType
from app.runtime_store import RuntimeStateStore, get_runtime_store


class CustomerPublishConfirmationService:
    def __init__(
        self,
        *,
        request_service: CustomerExecutionRequestService | None = None,
        execution_service: InMemoryDistributionExecutionService | None = None,
        creative_service: CreativeAssetService | None = None,
        creative_binding_service: CustomerCreativeBindingService | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._request_service = request_service or customer_execution_request_service
        self._execution_service = execution_service or distribution_execution_service
        self._creative_service = creative_service or creative_asset_service
        self._creative_binding_service = (
            creative_binding_service or customer_creative_binding_service
        )
        self._store = store or get_runtime_store()

    def view(
        self,
        *,
        project: dict,
        draft: CustomerStartingMoveDraftView | None,
    ) -> CustomerPreparedActionView:
        request, plan = self._current_plan(project=project, draft=draft)
        self._validate_exact(
            request=request,
            plan=plan,
            allow_approved=request.status in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"},
        )
        self._validate_confirmation_consistency(
            request=request,
            metadata=plan.action.operational_metadata,
        )
        creative = self._creative_for_view(request=request, plan=plan)
        if request.status in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"}:
            self._validate_creative_metadata(
                request=request,
                metadata=plan.action.operational_metadata,
                creative=creative,
            )
        return self._to_view(request=request, plan=plan, creative=creative)

    def confirm(
        self,
        *,
        project: dict,
        draft: CustomerStartingMoveDraftView | None,
        creative_asset_id: UUID | None = None,
    ) -> CustomerPreparedActionView:
        request, plan = self._current_plan(project=project, draft=draft)
        self._validate_exact(
            request=request,
            plan=plan,
            allow_approved=request.status in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"},
        )
        creative = self._creative_for_confirmation(
            request=request,
            plan=plan,
            requested_asset_id=creative_asset_id,
        )
        binding = (
            self._creative_binding_service.validate_exact_video(creative)
            if creative is not None
            else None
        )
        fingerprint = self._fingerprint(
            request=request,
            plan=plan,
            creative=creative,
            binding=binding,
        )
        metadata = dict(plan.action.operational_metadata)
        existing_fingerprint = metadata.get("customer_publish_confirmation_fingerprint")
        existing_confirmed_at = metadata.get("customer_publish_confirmed_at")

        if request.status in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"}:
            if request.customer_publish_confirmation_fingerprint != fingerprint:
                raise ValueError(
                    "Confirmed action fingerprint no longer matches the exact customer action."
                )
            if request.customer_publish_confirmed_at is None:
                raise ValueError("Customer confirmation record is missing its timestamp.")
            if existing_fingerprint or existing_confirmed_at:
                self._validate_confirmation_consistency(request=request, metadata=metadata)
                self._validate_creative_metadata(
                    request=request,
                    metadata=metadata,
                    creative=creative,
                )
            else:
                if request.status == "OPERATOR_APPROVED":
                    raise ValueError(
                        "Approved action is missing its durable customer confirmation stamp."
                    )
                self._persist_action_stamp(
                    plan=plan,
                    fingerprint=fingerprint,
                    confirmed_at=request.customer_publish_confirmed_at,
                    creative=creative,
                    binding=binding,
                )
            return self._to_view(request=request, plan=plan, creative=creative)

        if existing_fingerprint or existing_confirmed_at:
            if not existing_fingerprint or not existing_confirmed_at:
                raise ValueError(
                    "Prepared action contains an incomplete customer confirmation stamp."
                )
            if existing_fingerprint != fingerprint:
                raise ValueError(
                    "Prepared action was already confirmed with a different fingerprint."
                )
            confirmed_at = self._parse_confirmed_at(existing_confirmed_at)
        else:
            confirmed_at = datetime.now(UTC)

        request_updates = {
            "status": "PUBLISH_CONFIRMED",
            "customer_publish_confirmed_at": confirmed_at,
            "customer_publish_confirmation_fingerprint": fingerprint,
        }
        if creative is not None and binding is not None:
            request_updates.update(
                {
                    "confirmed_creative_asset_id": creative.id,
                    "confirmed_creative_asset_url": creative.public_url,
                    "confirmed_creative_brief_fingerprint": creative.brief_fingerprint,
                    "confirmed_creative_blob_id": binding.blob_id,
                    "confirmed_creative_sha256": binding.sha256,
                }
            )
        updated_request = request.model_copy(update=request_updates)
        self._store.put(
            CUSTOMER_EXECUTION_REQUEST_NAMESPACE,
            str(updated_request.id),
            updated_request.model_dump(mode="json"),
        )
        if not existing_fingerprint:
            self._persist_action_stamp(
                plan=plan,
                fingerprint=fingerprint,
                confirmed_at=confirmed_at,
                creative=creative,
                binding=binding,
            )
        return self._to_view(request=updated_request, plan=plan, creative=creative)

    def _current_plan(self, *, project: dict, draft: CustomerStartingMoveDraftView | None):
        request = self._request_service.view(project=project, draft=draft)
        if request is None:
            raise ValueError("Request preparation for the current accepted draft first.")
        if request.status not in {
            "ACTION_PREPARED",
            "PUBLISH_CONFIRMED",
            "OPERATOR_APPROVED",
        }:
            raise ValueError(
                "The requested action has not been prepared for customer confirmation yet."
            )
        if request.distribution_action_id is None or request.experiment_id is None:
            raise ValueError(
                "Prepared execution request is missing its action or experiment id."
            )
        try:
            plan = self._execution_service.get_plan(request.distribution_action_id)
        except KeyError as exc:
            raise ValueError("Prepared customer action could not be found.") from exc
        return request, plan

    def _validate_exact(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan,
        allow_approved: bool = False,
    ) -> None:
        action = plan.action
        experiment = plan.experiment
        prepared_pair = (
            action.status == DistributionActionStatus.PREPARED
            and experiment.status == DistributionExperimentStatus.DRAFT
        )
        approved_pair = (
            action.status == DistributionActionStatus.APPROVED
            and experiment.status == DistributionExperimentStatus.APPROVED
        )
        executed_pair = (
            action.status == DistributionActionStatus.EXECUTED
            and experiment.status == DistributionExperimentStatus.RUNNING
        )
        if not prepared_pair and not (allow_approved and (approved_pair or executed_pair)):
            raise ValueError(
                "Customer confirmation requires PREPARED/DRAFT, APPROVED/APPROVED, "
                "or EXECUTED/RUNNING state."
            )
        if request.distribution_action_id != action.id:
            raise ValueError("Prepared action does not match the customer execution request.")
        if request.experiment_id != experiment.id or action.experiment_id != experiment.id:
            raise ValueError(
                "Prepared action and experiment do not match the customer execution request."
            )
        if request.distribution_play_id != experiment.distribution_play_id:
            raise ValueError("Prepared action does not belong to the linked DistributionPlay.")
        if (
            request.opportunity_id != experiment.opportunity_id
            or action.opportunity_id != request.opportunity_id
        ):
            raise ValueError("Prepared action opportunity does not match the customer request.")
        if action.platform != request.platform:
            raise ValueError("Prepared action platform does not match the customer request.")
        if request.context_text is None:
            raise ValueError("Customer execution request is missing the exact accepted context.")
        if action.content_text != request.content_text:
            raise ValueError(
                "Prepared action content no longer matches the accepted customer draft."
            )
        if action.content_payload.get("context_text") != request.context_text:
            raise ValueError(
                "Prepared action context no longer matches the accepted customer draft."
            )
        expected_title = request.draft_title.strip() if request.draft_title else None
        if action.content_payload.get("title") != expected_title:
            raise ValueError(
                "Prepared action title no longer matches the accepted customer draft."
            )
        if str(action.target_url or "") != str(request.source_url):
            raise ValueError(
                "Prepared action target no longer matches the customer research source."
            )

        metadata = action.operational_metadata
        if metadata.get("customer_execution_request_id") != str(request.id):
            raise ValueError("Prepared action is not bound to this customer execution request.")
        if metadata.get("customer_exact_content_locked") is not True:
            raise ValueError("Prepared customer action must keep exact accepted content locked.")
        if metadata.get("customer_publish_confirmation_required") is not True:
            raise ValueError(
                "Prepared customer action must require final customer confirmation."
            )

    def _validate_confirmation_consistency(
        self,
        *,
        request: CustomerExecutionRequestView,
        metadata: dict,
    ) -> None:
        metadata_fingerprint = metadata.get("customer_publish_confirmation_fingerprint")
        metadata_confirmed_at = metadata.get("customer_publish_confirmed_at")
        creative_stamp = any(
            metadata.get(key)
            for key in (
                "customer_confirmed_creative_asset_id",
                "customer_confirmed_creative_asset_url",
                "customer_confirmed_creative_brief_fingerprint",
                "customer_confirmed_creative_blob_id",
                "customer_confirmed_creative_sha256",
            )
        )
        if request.status == "ACTION_PREPARED":
            if metadata_fingerprint or metadata_confirmed_at or creative_stamp:
                raise ValueError(
                    "Prepared action has a confirmation stamp without a durable customer record."
                )
            return
        if request.status not in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"}:
            raise ValueError("Customer action is not in a confirmable state.")
        if (
            request.customer_publish_confirmed_at is None
            or not request.customer_publish_confirmation_fingerprint
        ):
            raise ValueError("Customer confirmation record is incomplete.")
        if metadata_fingerprint != request.customer_publish_confirmation_fingerprint:
            raise ValueError(
                "Customer confirmation fingerprint does not match the prepared action."
            )
        if not metadata_confirmed_at:
            raise ValueError("Prepared action is missing its customer confirmation timestamp.")
        metadata_time = self._parse_confirmed_at(metadata_confirmed_at)
        if metadata_time != request.customer_publish_confirmed_at:
            raise ValueError("Customer confirmation timestamps do not match.")

    def _creative_for_view(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan,
    ) -> CreativeAssetView | None:
        if plan.action.action_type != DistributionActionType.ORGANIC_VIDEO:
            return None
        if request.status in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"}:
            return self._confirmed_creative(request=request, plan=plan)
        return self._reviewable_creative(plan=plan)

    def _creative_for_confirmation(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan,
        requested_asset_id: UUID | None,
    ) -> CreativeAssetView | None:
        if plan.action.action_type != DistributionActionType.ORGANIC_VIDEO:
            if requested_asset_id is not None:
                raise ValueError(
                    "This customer action does not include a confirmable creative asset."
                )
            return None
        if requested_asset_id is None:
            raise ValueError("Confirm the exact video asset shown in the customer review.")
        if request.status in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"}:
            creative = self._confirmed_creative(request=request, plan=plan)
        else:
            creative = self._reviewable_creative(plan=plan)
        if creative.id != requested_asset_id:
            raise ValueError(
                "Creative asset changed after customer review; refresh and confirm the exact "
                "video shown."
            )
        return creative

    def _reviewable_creative(self, *, plan) -> CreativeAssetView:
        readiness = self._creative_service.readiness(plan.action.id)
        if (
            readiness.status != CreativeReadinessStatus.READY
            or readiness.selected_asset is None
        ):
            raise ValueError("Exact customer video must be READY before publish confirmation.")
        asset = readiness.selected_asset
        if asset.action_id != plan.action.id or asset.status != CreativeAssetStatus.READY:
            raise ValueError("Reviewable customer video does not match the prepared action.")
        self._creative_binding_service.validate_exact_video(asset)
        return asset

    def _confirmed_creative(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan,
    ) -> CreativeAssetView:
        if (
            request.confirmed_creative_asset_id is None
            or request.confirmed_creative_asset_url is None
            or not request.confirmed_creative_brief_fingerprint
            or request.confirmed_creative_blob_id is None
            or not request.confirmed_creative_sha256
        ):
            raise ValueError("Customer-confirmed video binding is incomplete.")
        try:
            asset = self._creative_service.get_asset(request.confirmed_creative_asset_id)
        except KeyError as exc:
            raise ValueError("Customer-confirmed video asset could not be found.") from exc
        if asset.action_id != plan.action.id:
            raise ValueError("Customer-confirmed video no longer belongs to the exact action.")
        if asset.status != CreativeAssetStatus.READY:
            raise ValueError("Customer-confirmed video is no longer READY.")
        if asset.public_url is None or str(asset.public_url) != str(
            request.confirmed_creative_asset_url
        ):
            raise ValueError(
                "Customer-confirmed video URL no longer matches the reviewed asset."
            )
        if asset.brief_fingerprint != request.confirmed_creative_brief_fingerprint:
            raise ValueError(
                "Customer-confirmed video brief no longer matches the reviewed asset."
            )
        binding = self._creative_binding_service.validate_exact_video(asset)
        if binding.blob_id != request.confirmed_creative_blob_id:
            raise ValueError("Customer-confirmed video blob no longer matches the review.")
        if binding.sha256 != request.confirmed_creative_sha256:
            raise ValueError("Customer-confirmed video bytes no longer match the review.")
        return asset

    def _validate_creative_metadata(
        self,
        *,
        request: CustomerExecutionRequestView,
        metadata: dict,
        creative: CreativeAssetView | None,
    ) -> None:
        if creative is None:
            return
        binding = self._creative_binding_service.validate_exact_video(creative)
        if metadata.get("customer_confirmed_creative_asset_id") != str(creative.id):
            raise ValueError(
                "Customer-confirmed creative ID does not match the prepared action."
            )
        if metadata.get("customer_confirmed_creative_asset_url") != str(creative.public_url):
            raise ValueError(
                "Customer-confirmed creative URL does not match the prepared action."
            )
        if (
            metadata.get("customer_confirmed_creative_brief_fingerprint")
            != creative.brief_fingerprint
        ):
            raise ValueError(
                "Customer-confirmed creative brief does not match the prepared action."
            )
        if metadata.get("customer_confirmed_creative_blob_id") != str(binding.blob_id):
            raise ValueError(
                "Customer-confirmed creative blob does not match the prepared action."
            )
        if metadata.get("customer_confirmed_creative_sha256") != binding.sha256:
            raise ValueError(
                "Customer-confirmed creative bytes do not match the prepared action."
            )
        if request.confirmed_creative_asset_id != creative.id:
            raise ValueError("Customer creative record does not match the confirmed asset.")
        if request.confirmed_creative_blob_id != binding.blob_id:
            raise ValueError("Customer creative record does not match the confirmed blob.")
        if request.confirmed_creative_sha256 != binding.sha256:
            raise ValueError("Customer creative record does not match the confirmed bytes.")

    def _fingerprint(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan,
        creative: CreativeAssetView | None = None,
        binding=None,
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
            binding = binding or self._creative_binding_service.validate_exact_video(creative)
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

    def _persist_action_stamp(
        self,
        *,
        plan,
        fingerprint: str,
        confirmed_at: datetime,
        creative: CreativeAssetView | None = None,
        binding=None,
    ) -> None:
        metadata = dict(plan.action.operational_metadata)
        metadata.update(
            {
                "customer_publish_confirmed_at": confirmed_at.isoformat(),
                "customer_publish_confirmation_fingerprint": fingerprint,
            }
        )
        if creative is not None:
            binding = binding or self._creative_binding_service.validate_exact_video(creative)
            metadata.update(
                {
                    "customer_confirmed_creative_asset_id": str(creative.id),
                    "customer_confirmed_creative_asset_url": str(creative.public_url),
                    "customer_confirmed_creative_brief_fingerprint": creative.brief_fingerprint,
                    "customer_confirmed_creative_blob_id": str(binding.blob_id),
                    "customer_confirmed_creative_sha256": binding.sha256,
                }
            )
        updated_action = plan.action.model_copy(update={"operational_metadata": metadata})
        self._store.put(
            DISTRIBUTION_ACTION_NAMESPACE,
            str(updated_action.id),
            updated_action.model_dump(mode="json"),
        )
        plan.action.operational_metadata.clear()
        plan.action.operational_metadata.update(metadata)

    def _to_view(
        self,
        *,
        request: CustomerExecutionRequestView,
        plan,
        creative: CreativeAssetView | None = None,
    ) -> CustomerPreparedActionView:
        confirmed = request.status in {"PUBLISH_CONFIRMED", "OPERATOR_APPROVED"}
        approved = (
            plan.action.status == DistributionActionStatus.APPROVED
            and plan.experiment.status == DistributionExperimentStatus.APPROVED
        )
        executed = (
            plan.action.status == DistributionActionStatus.EXECUTED
            and plan.experiment.status == DistributionExperimentStatus.RUNNING
        )
        binding = (
            self._creative_binding_service.validate_exact_video(creative)
            if creative is not None
            else None
        )
        return CustomerPreparedActionView(
            request_id=request.id,
            project_id=request.project_id,
            distribution_action_id=plan.action.id,
            platform=request.platform,
            action_status=plan.action.status.value,
            source_title=request.source_title,
            source_url=request.source_url,
            target_url=plan.action.target_url,
            draft_title=plan.action.content_payload.get("title"),
            context_text=str(plan.action.content_payload.get("context_text") or ""),
            content_text=plan.action.content_text,
            creative_asset_id=creative.id if creative is not None else None,
            creative_asset_url=creative.public_url if creative is not None else None,
            creative_brief_fingerprint=(
                creative.brief_fingerprint if creative is not None else None
            ),
            creative_blob_id=binding.blob_id if binding is not None else None,
            creative_sha256=binding.sha256 if binding is not None else None,
            customer_publish_confirmed=confirmed,
            customer_publish_confirmed_at=(
                request.customer_publish_confirmed_at if confirmed else None
            ),
            operator_approved_at=request.operator_approved_at,
            execution_allowed=False,
            operator_approval_required=not (approved or executed),
            published=executed,
        )

    @staticmethod
    def _parse_confirmed_at(value) -> datetime:
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value))
            except ValueError as exc:
                raise ValueError(
                    "Prepared action has an invalid customer confirmation timestamp."
                ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed


customer_publish_confirmation_service = CustomerPublishConfirmationService()
