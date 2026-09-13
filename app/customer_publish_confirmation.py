from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256

from app.customer_channel_schemas import CustomerStartingMoveDraftView
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
from app.distribution_types import DistributionActionStatus
from app.runtime_store import RuntimeStateStore, get_runtime_store


class CustomerPublishConfirmationService:
    def __init__(
        self,
        *,
        request_service: CustomerExecutionRequestService | None = None,
        execution_service: InMemoryDistributionExecutionService | None = None,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._request_service = request_service or customer_execution_request_service
        self._execution_service = execution_service or distribution_execution_service
        self._store = store or get_runtime_store()

    def view(
        self,
        *,
        project: dict,
        draft: CustomerStartingMoveDraftView | None,
    ) -> CustomerPreparedActionView:
        request, plan = self._current_plan(project=project, draft=draft)
        self._validate_exact(request=request, plan=plan)
        self._validate_confirmation_consistency(
            request=request,
            metadata=plan.action.operational_metadata,
        )
        return self._to_view(request=request, plan=plan)

    def confirm(
        self,
        *,
        project: dict,
        draft: CustomerStartingMoveDraftView | None,
    ) -> CustomerPreparedActionView:
        request, plan = self._current_plan(project=project, draft=draft)
        self._validate_exact(request=request, plan=plan)
        fingerprint = self._fingerprint(request=request, plan=plan)
        metadata = dict(plan.action.operational_metadata)
        existing_fingerprint = metadata.get("customer_publish_confirmation_fingerprint")
        existing_confirmed_at = metadata.get("customer_publish_confirmed_at")

        if request.status == "PUBLISH_CONFIRMED":
            if request.customer_publish_confirmation_fingerprint != fingerprint:
                raise ValueError(
                    "Confirmed action fingerprint no longer matches the exact customer action."
                )
            if request.customer_publish_confirmed_at is None:
                raise ValueError("Customer confirmation record is missing its timestamp.")
            if existing_fingerprint or existing_confirmed_at:
                self._validate_confirmation_consistency(request=request, metadata=metadata)
            else:
                self._persist_action_stamp(
                    plan=plan,
                    fingerprint=fingerprint,
                    confirmed_at=request.customer_publish_confirmed_at,
                )
            return self._to_view(request=request, plan=plan)

        if existing_fingerprint or existing_confirmed_at:
            if not existing_fingerprint or not existing_confirmed_at:
                raise ValueError("Prepared action contains an incomplete customer confirmation stamp.")
            if existing_fingerprint != fingerprint:
                raise ValueError("Prepared action was already confirmed with a different fingerprint.")
            confirmed_at = self._parse_confirmed_at(existing_confirmed_at)
        else:
            confirmed_at = datetime.now(UTC)

        # Persist the customer record first. Until the action stamp below is durable, the
        # execution service approval guard remains locked and therefore fails closed.
        updated_request = request.model_copy(
            update={
                "status": "PUBLISH_CONFIRMED",
                "customer_publish_confirmed_at": confirmed_at,
                "customer_publish_confirmation_fingerprint": fingerprint,
            }
        )
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
            )
        return self._to_view(request=updated_request, plan=plan)

    def _current_plan(self, *, project: dict, draft: CustomerStartingMoveDraftView | None):
        request = self._request_service.view(project=project, draft=draft)
        if request is None:
            raise ValueError("Request preparation for the current accepted draft first.")
        if request.status not in {"ACTION_PREPARED", "PUBLISH_CONFIRMED"}:
            raise ValueError("The requested action has not been prepared for customer confirmation yet.")
        if request.distribution_action_id is None or request.experiment_id is None:
            raise ValueError("Prepared execution request is missing its action or experiment id.")
        try:
            plan = self._execution_service.get_plan(request.distribution_action_id)
        except KeyError as exc:
            raise ValueError("Prepared customer action could not be found.") from exc
        return request, plan

    def _validate_exact(self, *, request: CustomerExecutionRequestView, plan) -> None:
        action = plan.action
        experiment = plan.experiment
        if action.status != DistributionActionStatus.PREPARED:
            raise ValueError("Customer confirmation only accepts a PREPARED action.")
        if experiment.status != DistributionExperimentStatus.DRAFT:
            raise ValueError("Customer confirmation requires the experiment to remain DRAFT.")
        if request.distribution_action_id != action.id:
            raise ValueError("Prepared action does not match the customer execution request.")
        if request.experiment_id != experiment.id or action.experiment_id != experiment.id:
            raise ValueError("Prepared action and experiment do not match the customer execution request.")
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

    def _validate_confirmation_consistency(
        self,
        *,
        request: CustomerExecutionRequestView,
        metadata: dict,
    ) -> None:
        metadata_fingerprint = metadata.get("customer_publish_confirmation_fingerprint")
        metadata_confirmed_at = metadata.get("customer_publish_confirmed_at")
        if request.status == "ACTION_PREPARED":
            if metadata_fingerprint or metadata_confirmed_at:
                raise ValueError(
                    "Prepared action has a confirmation stamp without a durable customer record."
                )
            return
        if request.status != "PUBLISH_CONFIRMED":
            raise ValueError("Customer action is not in a confirmable state.")
        if (
            request.customer_publish_confirmed_at is None
            or not request.customer_publish_confirmation_fingerprint
        ):
            raise ValueError("Customer confirmation record is incomplete.")
        if metadata_fingerprint != request.customer_publish_confirmation_fingerprint:
            raise ValueError("Customer confirmation fingerprint does not match the prepared action.")
        if not metadata_confirmed_at:
            raise ValueError("Prepared action is missing its customer confirmation timestamp.")
        metadata_time = self._parse_confirmed_at(metadata_confirmed_at)
        if metadata_time != request.customer_publish_confirmed_at:
            raise ValueError("Customer confirmation timestamps do not match.")

    def _fingerprint(self, *, request: CustomerExecutionRequestView, plan) -> str:
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

    def _persist_action_stamp(self, *, plan, fingerprint: str, confirmed_at: datetime) -> None:
        metadata = dict(plan.action.operational_metadata)
        metadata.update(
            {
                "customer_publish_confirmed_at": confirmed_at.isoformat(),
                "customer_publish_confirmation_fingerprint": fingerprint,
            }
        )
        updated_action = plan.action.model_copy(update={"operational_metadata": metadata})
        self._store.put(
            DISTRIBUTION_ACTION_NAMESPACE,
            str(updated_action.id),
            updated_action.model_dump(mode="json"),
        )
        # The execution service caches actions. Change the cached model only after the
        # durable write succeeds so its approval guard observes the same persisted stamp.
        plan.action.operational_metadata.clear()
        plan.action.operational_metadata.update(metadata)

    def _to_view(self, *, request: CustomerExecutionRequestView, plan) -> CustomerPreparedActionView:
        confirmed = request.status == "PUBLISH_CONFIRMED"
        return CustomerPreparedActionView(
            request_id=request.id,
            project_id=request.project_id,
            distribution_action_id=plan.action.id,
            platform=request.platform,
            source_title=request.source_title,
            source_url=request.source_url,
            target_url=plan.action.target_url,
            draft_title=plan.action.content_payload.get("title"),
            context_text=str(plan.action.content_payload.get("context_text") or ""),
            content_text=plan.action.content_text,
            customer_publish_confirmed=confirmed,
            customer_publish_confirmed_at=(
                request.customer_publish_confirmed_at if confirmed else None
            ),
            execution_allowed=False,
            operator_approval_required=True,
            published=False,
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
