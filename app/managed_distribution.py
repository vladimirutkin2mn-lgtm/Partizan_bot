from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.config import Settings, get_settings
from app.distribution_control_plane_schemas import CampaignSlotCreateRequest
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import (
    CampaignSlotStatus,
    DistributionActionStatus,
    DistributionIdentityStatus,
    DistributionPlatform,
)
from app.managed_distribution_schemas import (
    CustomerManagedAssignmentView,
    ManagedAssignmentCreateRequest,
    ManagedAssignmentStatus,
    ManagedAssignmentView,
    ManagedCostBreakdown,
    ManagedFulfillmentRequest,
    ManagedPublisherHealth,
    ManagedPublisherRegistrationRequest,
    ManagedPublisherView,
    ManagedSelectionCandidateView,
    ManagedSelectionRequest,
    ManagedServiceStatusView,
)
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

MANAGED_PUBLISHER_NAMESPACE = "managed_distribution_publisher"
MANAGED_ASSIGNMENT_NAMESPACE = "managed_distribution_assignment"
MANAGED_SERVICE_LABEL = "Partizan Managed Distribution"
MANAGED_CAPACITY_WINDOW = timedelta(hours=24)


class ManagedDistributionError(RuntimeError):
    pass


class ManagedDistributionService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()
        self._publishers: dict[UUID, ManagedPublisherView] = {}
        self._assignments: dict[UUID, ManagedAssignmentView] = {}

    def readiness_blocker(self, platform: DistributionPlatform | None = None) -> str | None:
        if not self._settings.managed_distribution_public_ready:
            return "Partizan Managed Distribution is not enabled for customers yet"
        if not self._eligible_publishers(platform):
            suffix = f" for {platform.value}" if platform is not None else ""
            return f"no eligible managed publisher inventory is available{suffix}"
        return None

    def service_status(self, platform: DistributionPlatform) -> ManagedServiceStatusView:
        blocker = self.readiness_blocker(platform)
        return ManagedServiceStatusView(
            platform=platform,
            service_label=MANAGED_SERVICE_LABEL,
            available=blocker is None,
            blocker=blocker,
        )

    def register_publisher(
        self,
        payload: ManagedPublisherRegistrationRequest,
    ) -> ManagedPublisherView:
        try:
            identity = distribution_control_plane_service.get_identity(
                payload.distribution_identity_id
            )
        except KeyError as exc:
            raise ManagedDistributionError("Distribution Identity not found") from exc
        if identity.status != DistributionIdentityStatus.ACTIVE:
            raise ManagedDistributionError(
                "Managed publisher requires an ACTIVE Distribution Identity"
            )

        allowed_kinds = set(identity.eligibility.get("allowed_opportunity_kinds") or [])
        allowed_actions = set(identity.eligibility.get("allowed_actions") or [])
        for surface in payload.allowed_surfaces:
            if surface.value not in allowed_kinds:
                raise ManagedDistributionError(
                    f"Managed surface {surface.value} is not allowed by the Distribution Identity"
                )
        for action in payload.allowed_actions:
            if action.value not in allowed_actions:
                raise ManagedDistributionError(
                    f"Managed action {action.value} is not allowed by the Distribution Identity"
                )
        if any(
            row.distribution_identity_id == identity.id for row in self.list_publishers()
        ):
            raise ManagedDistributionError(
                "Distribution Identity is already registered as managed inventory"
            )

        now = datetime.now(UTC)
        publisher = ManagedPublisherView(
            id=uuid4(),
            distribution_identity_id=identity.id,
            ownership=payload.ownership,
            internal_label=payload.internal_label.strip(),
            platform=identity.platform,
            topic_verticals=self._normalized_values(payload.topic_verticals),
            languages=self._normalized_values(payload.languages),
            allowed_surfaces=payload.allowed_surfaces,
            allowed_actions=payload.allowed_actions,
            daily_action_capacity=payload.daily_action_capacity,
            prior_outcome_score=payload.prior_outcome_score,
            last_activity_at=payload.last_activity_at,
            health=ManagedPublisherHealth.ELIGIBLE,
            partner_reference=(
                payload.partner_reference.strip()
                if payload.partner_reference is not None
                else None
            ),
            created_at=now,
            updated_at=now,
        )
        self._publishers[publisher.id] = publisher
        self._persist_publisher(publisher)
        return publisher

    def get_publisher(self, publisher_id: UUID) -> ManagedPublisherView:
        cached = self._publishers.get(publisher_id)
        if cached is not None:
            return cached
        payload = self._store.get(MANAGED_PUBLISHER_NAMESPACE, str(publisher_id))
        if payload is None:
            raise KeyError(publisher_id)
        publisher = ManagedPublisherView.model_validate(payload)
        self._publishers[publisher.id] = publisher
        return publisher

    def list_publishers(
        self,
        platform: DistributionPlatform | None = None,
    ) -> list[ManagedPublisherView]:
        self._hydrate_publishers()
        rows = list(self._publishers.values())
        if platform is not None:
            rows = [row for row in rows if row.platform == platform]
        return sorted(
            rows,
            key=lambda row: (row.platform.value, row.internal_label, str(row.id)),
        )

    def set_health(
        self,
        publisher_id: UUID,
        health: ManagedPublisherHealth,
        reason: str | None = None,
    ) -> ManagedPublisherView:
        publisher = self.get_publisher(publisher_id)
        updated = publisher.model_copy(
            update={
                "health": health,
                "health_reason": str(reason or "").strip() or None,
                "updated_at": datetime.now(UTC),
            }
        )
        self._publishers[publisher_id] = updated
        self._persist_publisher(updated)
        return updated

    def select_candidates(
        self,
        payload: ManagedSelectionRequest,
    ) -> list[ManagedSelectionCandidateView]:
        candidates: list[ManagedSelectionCandidateView] = []
        for publisher in self._eligible_publishers(payload.platform):
            if payload.action_type not in publisher.allowed_actions:
                continue
            if payload.opportunity_kind not in publisher.allowed_surfaces:
                continue
            if not self._language_matches(payload.language, publisher.languages):
                continue
            if self._has_reserved_assignment(publisher.id):
                continue
            remaining = self.capacity_remaining_24h(publisher.id)
            if remaining <= 0:
                continue
            score, reasons = self._selection_score(
                publisher,
                vertical=payload.vertical,
                capacity_remaining=remaining,
            )
            candidates.append(
                ManagedSelectionCandidateView(
                    managed_publisher_id=publisher.id,
                    distribution_identity_id=publisher.distribution_identity_id,
                    ownership=publisher.ownership,
                    score=score,
                    capacity_remaining_24h=remaining,
                    reasons=reasons,
                )
            )
        return sorted(
            candidates,
            key=lambda item: (-item.score, str(item.managed_publisher_id)),
        )

    def reserve(
        self,
        product_id: UUID,
        payload: ManagedAssignmentCreateRequest,
    ) -> ManagedAssignmentView:
        blocker = self.readiness_blocker(payload.platform)
        if blocker is not None:
            raise ManagedDistributionError(blocker)
        try:
            product_intake_service.get_product(product_id)
        except KeyError as exc:
            raise ManagedDistributionError("Product not found") from exc

        candidates = self.select_candidates(payload)
        if not candidates:
            raise ManagedDistributionError(
                "No eligible managed publisher satisfies fit, health, policy surface, "
                "conflict and capacity gates"
            )
        publisher = self.get_publisher(candidates[0].managed_publisher_id)
        try:
            slot = distribution_control_plane_service.create_campaign_slot(
                product_id,
                CampaignSlotCreateRequest(
                    distribution_identity_id=publisher.distribution_identity_id,
                    status=CampaignSlotStatus.ACTIVE,
                    metadata={
                        "managed_distribution": True,
                        "managed_publisher_id": str(publisher.id),
                        "ownership": publisher.ownership.value,
                        "conflict_group": payload.conflict_group,
                    },
                ),
            )
        except ValueError as exc:
            raise ManagedDistributionError(str(exc)) from exc

        assignment = ManagedAssignmentView(
            id=uuid4(),
            product_id=product_id,
            managed_publisher_id=publisher.id,
            distribution_identity_id=publisher.distribution_identity_id,
            ownership=publisher.ownership,
            platform=publisher.platform,
            action_type=payload.action_type,
            opportunity_kind=payload.opportunity_kind,
            opportunity_id=payload.opportunity_id,
            campaign_slot_id=slot.id,
            conflict_group=str(payload.conflict_group or "").strip() or None,
            status=ManagedAssignmentStatus.RESERVED,
            reserved_at=datetime.now(UTC),
        )
        self._assignments[assignment.id] = assignment
        self._persist_assignment(assignment)
        return assignment

    def fulfill(
        self,
        assignment_id: UUID,
        payload: ManagedFulfillmentRequest,
    ) -> ManagedAssignmentView:
        assignment = self.get_assignment(assignment_id)
        if assignment.status != ManagedAssignmentStatus.RESERVED:
            raise ManagedDistributionError("Only RESERVED managed assignments can be fulfilled")
        try:
            action = distribution_execution_service.get_action(payload.action_id)
        except KeyError as exc:
            raise ManagedDistributionError("DistributionAction not found") from exc
        if action.status != DistributionActionStatus.APPROVED:
            raise ManagedDistributionError(
                "Managed fulfillment requires an APPROVED DistributionAction"
            )
        if action.platform != assignment.platform:
            raise ManagedDistributionError("DistributionAction platform does not match assignment")
        if action.distribution_identity_id != assignment.distribution_identity_id:
            raise ManagedDistributionError(
                "DistributionAction is not assigned to the reserved managed publisher"
            )
        if action.experiment_id is None:
            raise ManagedDistributionError("DistributionAction has no experiment")
        experiment = distribution_execution_service.get_experiment(action.experiment_id)
        if experiment.product_id != assignment.product_id:
            raise ManagedDistributionError("DistributionAction does not belong to assignment product")

        execution = distribution_execution_service.mark_executed(
            action.id,
            DistributionActionExecutionRequest(
                external_reference=payload.external_reference,
                executed_url=payload.executed_url,
                notes=payload.notes,
            ),
        )
        now = execution.action.executed_at or datetime.now(UTC)
        distribution_execution_service.record_external_observation(
            action.id,
            provider="partizan_managed",
            observation={
                "assignment_id": str(assignment.id),
                "ownership": assignment.ownership.value,
                "service": MANAGED_SERVICE_LABEL,
                "fulfilled_at": now.isoformat(),
            },
        )
        distribution_control_plane_service.set_campaign_slot_status(
            assignment.campaign_slot_id,
            CampaignSlotStatus.COMPLETED,
        )
        updated = assignment.model_copy(
            update={
                "status": ManagedAssignmentStatus.FULFILLED,
                "action_id": action.id,
                "external_reference": payload.external_reference,
                "executed_url": payload.executed_url,
                "cost": ManagedCostBreakdown(
                    distribution_spend_usd=payload.distribution_spend_usd,
                    operational_cost_usd=payload.operational_cost_usd,
                    management_fee_usd=payload.management_fee_usd,
                ),
                "fulfilled_at": now,
            }
        )
        self._assignments[assignment.id] = updated
        self._persist_assignment(updated)
        return updated

    def release(self, assignment_id: UUID) -> ManagedAssignmentView:
        assignment = self.get_assignment(assignment_id)
        if assignment.status != ManagedAssignmentStatus.RESERVED:
            raise ManagedDistributionError("Only RESERVED managed assignments can be released")
        distribution_control_plane_service.set_campaign_slot_status(
            assignment.campaign_slot_id,
            CampaignSlotStatus.CANCELLED,
        )
        updated = assignment.model_copy(
            update={
                "status": ManagedAssignmentStatus.RELEASED,
                "released_at": datetime.now(UTC),
            }
        )
        self._assignments[assignment.id] = updated
        self._persist_assignment(updated)
        return updated

    def get_assignment(self, assignment_id: UUID) -> ManagedAssignmentView:
        cached = self._assignments.get(assignment_id)
        if cached is not None:
            return cached
        payload = self._store.get(MANAGED_ASSIGNMENT_NAMESPACE, str(assignment_id))
        if payload is None:
            raise KeyError(assignment_id)
        assignment = ManagedAssignmentView.model_validate(payload)
        self._assignments[assignment.id] = assignment
        return assignment

    def list_assignments(self, product_id: UUID | None = None) -> list[ManagedAssignmentView]:
        self._hydrate_assignments()
        rows = list(self._assignments.values())
        if product_id is not None:
            rows = [row for row in rows if row.product_id == product_id]
        return sorted(rows, key=lambda row: (row.reserved_at, str(row.id)))

    def list_customer_assignments(self, product_id: UUID) -> list[CustomerManagedAssignmentView]:
        return [
            CustomerManagedAssignmentView(
                id=row.id,
                platform=row.platform,
                service_label=MANAGED_SERVICE_LABEL,
                ownership=row.ownership,
                status=row.status,
                cost=row.cost,
                executed_url=row.executed_url,
                fulfilled_at=row.fulfilled_at,
            )
            for row in self.list_assignments(product_id)
        ]

    def capacity_remaining_24h(self, publisher_id: UUID) -> int:
        publisher = self.get_publisher(publisher_id)
        cutoff = datetime.now(UTC) - MANAGED_CAPACITY_WINDOW
        fulfilled = sum(
            1
            for row in self.list_assignments()
            if row.managed_publisher_id == publisher_id
            and row.status == ManagedAssignmentStatus.FULFILLED
            and row.fulfilled_at is not None
            and self._utc(row.fulfilled_at) >= cutoff
        )
        return max(0, publisher.daily_action_capacity - fulfilled)

    def _eligible_publishers(
        self,
        platform: DistributionPlatform | None,
    ) -> list[ManagedPublisherView]:
        eligible: list[ManagedPublisherView] = []
        for row in self.list_publishers(platform):
            if row.health != ManagedPublisherHealth.ELIGIBLE:
                continue
            try:
                identity = distribution_control_plane_service.get_identity(
                    row.distribution_identity_id
                )
            except KeyError:
                continue
            if identity.status == DistributionIdentityStatus.ACTIVE:
                eligible.append(row)
        return eligible

    def _has_reserved_assignment(self, publisher_id: UUID) -> bool:
        return any(
            row.managed_publisher_id == publisher_id
            and row.status == ManagedAssignmentStatus.RESERVED
            for row in self.list_assignments()
        )

    def _selection_score(
        self,
        publisher: ManagedPublisherView,
        *,
        vertical: str,
        capacity_remaining: int,
    ) -> tuple[float, list[str]]:
        requested = self._tokens(vertical)
        available = self._tokens(" ".join(publisher.topic_verticals))
        overlap = len(requested & available)
        fit = (
            min(40.0, 20.0 + (20.0 * overlap / max(1, len(requested))))
            if requested and overlap
            else 10.0
        )
        fit_reason = "topic/vertical overlap" if overlap else "broad vertical fallback"
        outcome = 30.0 * (publisher.prior_outcome_score / 100.0)
        recency = self._recency_score(publisher.last_activity_at)
        capacity = 10.0 * min(1.0, capacity_remaining / publisher.daily_action_capacity)
        total = round(min(100.0, fit + outcome + recency + capacity), 2)
        return total, [
            fit_reason,
            f"prior outcome {publisher.prior_outcome_score:.0f}/100",
            f"recent activity contribution {recency:.0f}/20",
            f"capacity remaining {capacity_remaining}/{publisher.daily_action_capacity}",
        ]

    @classmethod
    def _recency_score(cls, last_activity_at: datetime | None) -> float:
        if last_activity_at is None:
            return 0.0
        age = datetime.now(UTC) - cls._utc(last_activity_at)
        if age <= timedelta(days=7):
            return 20.0
        if age <= timedelta(days=30):
            return 10.0
        if age <= timedelta(days=90):
            return 5.0
        return 0.0

    @staticmethod
    def _language_matches(requested: str, languages: list[str]) -> bool:
        value = requested.strip().casefold()
        return value in {item.strip().casefold() for item in languages}

    @staticmethod
    def _tokens(value: str) -> set[str]:
        normalized = "".join(char.lower() if char.isalnum() else " " for char in value)
        return {token for token in normalized.split() if len(token) >= 3}

    @staticmethod
    def _normalized_values(values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = value.strip()
            key = normalized.casefold()
            if normalized and key not in seen:
                result.append(normalized)
                seen.add(key)
        return result

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _hydrate_publishers(self) -> None:
        for payload in self._store.list_namespace(MANAGED_PUBLISHER_NAMESPACE):
            row = ManagedPublisherView.model_validate(payload)
            self._publishers[row.id] = row

    def _hydrate_assignments(self) -> None:
        for payload in self._store.list_namespace(MANAGED_ASSIGNMENT_NAMESPACE):
            row = ManagedAssignmentView.model_validate(payload)
            self._assignments[row.id] = row

    def _persist_publisher(self, publisher: ManagedPublisherView) -> None:
        self._store.put(
            MANAGED_PUBLISHER_NAMESPACE,
            str(publisher.id),
            publisher.model_dump(mode="json"),
        )

    def _persist_assignment(self, assignment: ManagedAssignmentView) -> None:
        self._store.put(
            MANAGED_ASSIGNMENT_NAMESPACE,
            str(assignment.id),
            assignment.model_dump(mode="json"),
        )

    def reset(self) -> None:
        self._publishers.clear()
        self._assignments.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(MANAGED_PUBLISHER_NAMESPACE)
            self._store.clear_namespace(MANAGED_ASSIGNMENT_NAMESPACE)


managed_distribution_service = ManagedDistributionService()
