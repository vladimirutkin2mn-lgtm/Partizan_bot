from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.customer_channel_schemas import (
    CustomerStartingMoveDraftView,
    CustomerStartingMoveSetupView,
)
from app.customer_execution_request_schemas import CustomerExecutionRequestView
from app.distribution_execution_schemas import DistributionExecutionPlanView
from app.distribution_play_schemas import DistributionPlayStatus, DistributionPlayView
from app.distribution_schemas import DistributionOpportunityView
from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_EXECUTION_REQUEST_NAMESPACE = "customer_execution_request"


class CustomerExecutionRequestService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def view(
        self,
        *,
        project: dict,
        draft: CustomerStartingMoveDraftView | None,
    ) -> CustomerExecutionRequestView | None:
        if draft is None or draft.review_status != "ACCEPTED":
            return None
        rows = [
            row
            for row in self.list_requests()
            if row.project_id == UUID(str(project["id"]))
            and row.platform == draft.platform
            and str(row.source_url) == str(draft.source_url)
            and row.content_text == draft.content_text
        ]
        if not rows:
            return None
        return max(rows, key=lambda row: row.requested_at)

    def request(
        self,
        *,
        project: dict,
        draft: CustomerStartingMoveDraftView | None,
        setup: CustomerStartingMoveSetupView | None,
    ) -> CustomerExecutionRequestView:
        if draft is None or draft.review_status != "ACCEPTED":
            raise ValueError("Accept the starting-move draft before requesting preparation.")
        if setup is None or setup.platform != draft.platform:
            raise ValueError("Review execution setup before requesting preparation.")
        if setup.state != "READY_FOR_HANDOFF":
            raise ValueError("Complete the required channel setup before requesting preparation.")

        product_id_raw = project.get("product_id")
        if not product_id_raw:
            raise ValueError("A product is required before requesting preparation.")

        existing = self.view(project=project, draft=draft)
        if existing is not None:
            return existing

        request = CustomerExecutionRequestView(
            id=uuid4(),
            project_id=UUID(str(project["id"])),
            product_id=UUID(str(product_id_raw)),
            platform=draft.platform,
            publisher_mode=setup.publisher_mode,
            source_title=draft.source_title,
            source_url=draft.source_url,
            draft_title=draft.title,
            context_text=draft.context_text,
            content_text=draft.content_text,
            execution_allowed=False,
            customer_publish_confirmation_required=True,
            requested_at=datetime.now(UTC),
        )
        self._persist(request)
        return request

    def get_request(self, request_id: UUID) -> CustomerExecutionRequestView:
        payload = self._store.get(CUSTOMER_EXECUTION_REQUEST_NAMESPACE, str(request_id))
        if payload is None:
            raise KeyError(request_id)
        return CustomerExecutionRequestView.model_validate(payload)

    def link_preparation(
        self,
        *,
        request_id: UUID,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
    ) -> CustomerExecutionRequestView:
        request = self.get_request(request_id)
        if request.status in {"PREPARATION_READY", "ACTION_PREPARED"}:
            if (
                request.distribution_play_id == play.id
                and request.opportunity_id == opportunity.id
            ):
                self.validate_linked_preparation(
                    request=request,
                    play=play,
                    opportunity=opportunity,
                )
                return request
            raise ValueError("Execution request is already linked to a different preparation play.")
        if request.status != "REQUESTED":
            raise ValueError("Only REQUESTED execution requests can be linked for preparation.")
        self._validate_domain_match(request=request, play=play, opportunity=opportunity)

        updated = request.model_copy(
            update={
                "status": "PREPARATION_READY",
                "distribution_play_id": play.id,
                "opportunity_id": opportunity.id,
                "preparation_ready_at": datetime.now(UTC),
            }
        )
        self._persist(updated)
        return updated

    def validate_linked_preparation(
        self,
        *,
        request: CustomerExecutionRequestView,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
    ) -> None:
        if request.status not in {"PREPARATION_READY", "ACTION_PREPARED"}:
            raise ValueError("Execution request is not ready for action preparation.")
        if request.distribution_play_id != play.id:
            raise ValueError("Linked DistributionPlay does not match the execution request.")
        if request.opportunity_id != opportunity.id:
            raise ValueError("Linked opportunity does not match the execution request.")
        self._validate_domain_match(request=request, play=play, opportunity=opportunity)

    def mark_action_prepared(
        self,
        *,
        request_id: UUID,
        plan: DistributionExecutionPlanView,
    ) -> CustomerExecutionRequestView:
        request = self.get_request(request_id)
        if request.status == "ACTION_PREPARED":
            if (
                request.distribution_action_id == plan.action.id
                and request.experiment_id == plan.experiment.id
            ):
                return request
            raise ValueError("Execution request is already linked to a different prepared action.")
        if request.status != "PREPARATION_READY":
            raise ValueError("Execution request must be PREPARATION_READY before action preparation.")
        if request.distribution_play_id != plan.experiment.distribution_play_id:
            raise ValueError("Prepared action does not belong to the linked DistributionPlay.")
        if request.opportunity_id != plan.experiment.opportunity_id:
            raise ValueError("Prepared action does not belong to the linked opportunity.")
        if plan.action.opportunity_id != request.opportunity_id:
            raise ValueError("Prepared action opportunity does not match the customer request.")
        if plan.action.content_text != request.content_text:
            raise ValueError("Prepared action content must exactly match the accepted customer draft.")
        if request.context_text is not None and (
            plan.action.content_payload.get("context_text") != request.context_text
        ):
            raise ValueError("Prepared action context must exactly match the accepted customer draft.")
        expected_title = request.draft_title.strip() if request.draft_title else None
        actual_title = plan.action.content_payload.get("title")
        if actual_title != expected_title:
            raise ValueError("Prepared action title must exactly match the accepted customer draft.")
        if str(plan.action.target_url or "") != str(request.source_url):
            raise ValueError("Prepared action target must match the customer research source.")

        metadata = plan.action.operational_metadata
        if metadata.get("customer_execution_request_id") != str(request.id):
            raise ValueError("Prepared action is not bound to this customer execution request.")
        if metadata.get("customer_exact_content_locked") is not True:
            raise ValueError("Prepared customer action must keep exact accepted content locked.")
        if metadata.get("customer_publish_confirmation_required") is not True:
            raise ValueError("Prepared customer action must require final customer confirmation.")

        updated = request.model_copy(
            update={
                "status": "ACTION_PREPARED",
                "distribution_action_id": plan.action.id,
                "experiment_id": plan.experiment.id,
                "action_prepared_at": datetime.now(UTC),
            }
        )
        self._persist(updated)
        return updated

    def list_requests(self) -> list[CustomerExecutionRequestView]:
        rows: list[CustomerExecutionRequestView] = []
        for payload in self._store.list_namespace(CUSTOMER_EXECUTION_REQUEST_NAMESPACE):
            try:
                rows.append(CustomerExecutionRequestView.model_validate(payload))
            except ValueError:
                continue
        return sorted(rows, key=lambda row: (row.requested_at, str(row.id)))

    def _validate_domain_match(
        self,
        *,
        request: CustomerExecutionRequestView,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView,
    ) -> None:
        if play.status != DistributionPlayStatus.READY:
            raise ValueError("Only a READY DistributionPlay can be linked for preparation.")
        if play.product_id != request.product_id:
            raise ValueError("DistributionPlay does not belong to the requested product.")
        if play.platform != request.platform:
            raise ValueError("DistributionPlay platform does not match the customer request.")
        if play.opportunity_id != opportunity.id:
            raise ValueError("DistributionPlay opportunity does not match the validated opportunity.")
        if opportunity.platform != request.platform:
            raise ValueError("Opportunity platform does not match the customer request.")
        if opportunity.url is None or str(opportunity.url) != str(request.source_url):
            raise ValueError("DistributionPlay opportunity does not match the customer research source.")

    def _persist(self, request: CustomerExecutionRequestView) -> None:
        self._store.put(
            CUSTOMER_EXECUTION_REQUEST_NAMESPACE,
            str(request.id),
            request.model_dump(mode="json"),
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CUSTOMER_EXECUTION_REQUEST_NAMESPACE)


customer_execution_request_service = CustomerExecutionRequestService()
