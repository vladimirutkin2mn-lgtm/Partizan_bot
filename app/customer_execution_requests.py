from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.customer_channel_schemas import (
    CustomerStartingMoveDraftView,
    CustomerStartingMoveSetupView,
)
from app.customer_execution_request_schemas import CustomerExecutionRequestView
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
            content_text=draft.content_text,
            execution_allowed=False,
            customer_publish_confirmation_required=True,
            requested_at=datetime.now(UTC),
        )
        self._store.put(
            CUSTOMER_EXECUTION_REQUEST_NAMESPACE,
            str(request.id),
            request.model_dump(mode="json"),
        )
        return request

    def list_requests(self) -> list[CustomerExecutionRequestView]:
        rows: list[CustomerExecutionRequestView] = []
        for payload in self._store.list_namespace(CUSTOMER_EXECUTION_REQUEST_NAMESPACE):
            try:
                rows.append(CustomerExecutionRequestView.model_validate(payload))
            except ValueError:
                continue
        return sorted(rows, key=lambda row: (row.requested_at, str(row.id)))

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CUSTOMER_EXECUTION_REQUEST_NAMESPACE)


customer_execution_request_service = CustomerExecutionRequestService()
