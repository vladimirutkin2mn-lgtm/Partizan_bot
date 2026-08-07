from uuid import UUID, uuid4

from app.channel_service import channel_service
from app.execution import (
    ContactExtractor,
    OutreachComposer,
    TrackingLinkBuilder,
    get_delivery_provider,
)
from app.growth_play_service import growth_play_service
from app.llm import get_llm_provider
from app.schemas import (
    ExecutionContactView,
    ExecutionPackageView,
    ExecutionPrepareRequest,
    ExecutionRunResponse,
    ExperimentView,
    GrowthPlayView,
    ProductProfileView,
)


class InMemoryExecutionService:
    def __init__(self) -> None:
        self._packages: dict[UUID, ExecutionPackageView] = {}
        self._experiments: dict[UUID, ExperimentView] = {}
        self._package_by_play: dict[UUID, UUID] = {}

    async def prepare(
        self,
        product: ProductProfileView,
        play: GrowthPlayView,
        payload: ExecutionPrepareRequest,
    ) -> ExecutionPackageView:
        if play.status != "APPROVED":
            raise ValueError("Growth Play must be APPROVED before execution preparation")
        if play.id in self._package_by_play:
            raise ValueError("Execution package already exists for this Growth Play")

        channel_result = channel_service.get(product.id)
        channel = next(
            (item for item in channel_result.opportunities if item.id == play.channel_id),
            None,
        )
        if channel is None:
            raise KeyError(play.channel_id)

        destination_url = self._resolve_destination(product, payload)
        contact = ContactExtractor().extract(
            channel,
            override_email=payload.contact_email,
            override_name=payload.contact_name,
        )
        tracking_url, referral_token = TrackingLinkBuilder().build(
            destination_url=destination_url,
            product_id=product.id,
            play=play,
        )
        draft = await OutreachComposer(get_llm_provider()).compose(
            product=product,
            play=play,
            channel=channel,
            contact=contact,
            tracking_url=tracking_url,
        )

        package_id = uuid4()
        experiment_id = uuid4()
        package = ExecutionPackageView(
            id=package_id,
            product_id=product.id,
            play_id=play.id,
            experiment_id=experiment_id,
            contact=ExecutionContactView(
                method=contact.method,
                address=contact.address,
                name=contact.name,
                contact_url=contact.contact_url,
                source=contact.source,
            ),
            subject=draft.subject,
            body=draft.body,
            tracking_url=tracking_url,
            referral_token=referral_token,
            status="PREPARED",
        )
        experiment = ExperimentView(
            id=experiment_id,
            product_id=product.id,
            growth_play_id=play.id,
            execution_package_id=package_id,
            status="DRAFT",
            tracking_url=tracking_url,
        )
        self._packages[package_id] = package
        self._experiments[experiment_id] = experiment
        self._package_by_play[play.id] = package_id
        return package

    def edit(self, package_id: UUID, subject: str, body: str) -> ExecutionPackageView:
        package = self._packages[package_id]
        if package.status != "PREPARED":
            raise ValueError("Only PREPARED packages can be edited")
        updated = package.model_copy(update={"subject": subject, "body": body})
        self._packages[package_id] = updated
        return updated

    def approve(self, package_id: UUID) -> ExecutionPackageView:
        package = self._packages[package_id]
        if package.status != "PREPARED":
            raise ValueError("Only PREPARED packages can be approved")
        updated = package.model_copy(update={"status": "APPROVED"})
        experiment = self._experiments[package.experiment_id].model_copy(
            update={"status": "APPROVED"}
        )
        self._packages[package_id] = updated
        self._experiments[experiment.id] = experiment
        return updated

    def reject(self, package_id: UUID) -> ExecutionPackageView:
        package = self._packages[package_id]
        if package.status not in {"PREPARED", "APPROVED"}:
            raise ValueError("Package can no longer be rejected")
        updated = package.model_copy(update={"status": "REJECTED"})
        experiment = self._experiments[package.experiment_id].model_copy(
            update={"status": "CANCELLED"}
        )
        self._packages[package_id] = updated
        self._experiments[experiment.id] = experiment
        return updated

    async def run(self, package_id: UUID) -> ExecutionRunResponse:
        package = self._packages[package_id]
        if package.status != "APPROVED":
            raise ValueError("Execution package must be APPROVED before Run")
        if package.contact.method != "email" or not package.contact.address:
            raise ValueError(
                "This package has no email contact; platform delivery remains manual in Milestone 5"
            )

        provider = get_delivery_provider()
        try:
            delivery_id = await provider.send_email(
                to_email=package.contact.address,
                subject=package.subject,
                body=package.body,
            )
        except Exception:
            self._packages[package_id] = package.model_copy(update={"status": "FAILED"})
            raise

        updated_package = package.model_copy(
            update={"status": "SENT", "delivery_id": delivery_id}
        )
        experiment = self._experiments[package.experiment_id].model_copy(
            update={"status": "RUNNING", "delivery_id": delivery_id}
        )
        self._packages[package_id] = updated_package
        self._experiments[experiment.id] = experiment
        return ExecutionRunResponse(package=updated_package, experiment=experiment)

    def get_package(self, package_id: UUID) -> ExecutionPackageView:
        return self._packages[package_id]

    def get_experiment(self, experiment_id: UUID) -> ExperimentView:
        return self._experiments[experiment_id]

    def _resolve_destination(
        self,
        product: ProductProfileView,
        payload: ExecutionPrepareRequest,
    ) -> str:
        if payload.destination_url is not None:
            return str(payload.destination_url)
        if product.reference_links:
            return product.reference_links[0]
        raise ValueError(
            "destination_url is required when ProductProfile has no reference link"
        )

    def reset(self) -> None:
        self._packages.clear()
        self._experiments.clear()
        self._package_by_play.clear()


def find_growth_play(product_id: UUID, play_id: UUID) -> GrowthPlayView:
    result = growth_play_service.get(product_id)
    play = next((item for item in result.plays if item.id == play_id), None)
    if play is None:
        raise KeyError(play_id)
    return play


execution_service = InMemoryExecutionService()
