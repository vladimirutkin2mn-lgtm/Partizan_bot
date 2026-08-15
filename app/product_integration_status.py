from uuid import UUID

from pydantic import BaseModel

from app.config import Settings
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_event_ingestion import distribution_event_key_service


class ProductIntegrationFunnelView(BaseModel):
    visits: int = 0
    signups: int = 0
    activated_users: int = 0
    paid_users: int = 0


class ProductIntegrationStatusView(BaseModel):
    product_id: UUID
    event_key_configured: bool
    public_tracking_configured: bool
    public_base_url: str | None = None
    experiment_count: int
    ready_for_attributed_conversions: bool
    funnel: ProductIntegrationFunnelView
    observed_event_types: list[str]
    unobserved_event_types: list[str]
    blockers: list[str]


class ProductIntegrationStatusService:
    def get(self, product_id: UUID, settings: Settings) -> ProductIntegrationStatusView:
        key_status = distribution_event_key_service.status(product_id)
        analytics = distribution_analytics_service.product_analytics(product_id)

        funnel = ProductIntegrationFunnelView(
            visits=sum(item.metrics.visits for item in analytics.experiments),
            signups=sum(item.metrics.signups for item in analytics.experiments),
            activated_users=sum(item.metrics.activated_users for item in analytics.experiments),
            paid_users=sum(item.metrics.paid_users for item in analytics.experiments),
        )
        observations = {
            "VISIT": funnel.visits > 0,
            "SIGNUP": funnel.signups > 0,
            "ACTIVATED": funnel.activated_users > 0,
            "PAID": funnel.paid_users > 0,
        }
        observed = [event_type for event_type, seen in observations.items() if seen]
        unobserved = [event_type for event_type, seen in observations.items() if not seen]

        public_tracking_configured = settings.partizan_public_base_url is not None
        blockers: list[str] = []
        if not key_status.configured:
            blockers.append("Create a Product Event Key for server-to-server conversions")
        if not public_tracking_configured:
            blockers.append("Configure PARTIZAN_PUBLIC_BASE_URL for first-click VISIT attribution")
        if analytics.experiment_count == 0:
            blockers.append("Create a DistributionExperiment before verifying attributed events")

        return ProductIntegrationStatusView(
            product_id=product_id,
            event_key_configured=key_status.configured,
            public_tracking_configured=public_tracking_configured,
            public_base_url=settings.partizan_public_base_url,
            experiment_count=analytics.experiment_count,
            ready_for_attributed_conversions=(
                key_status.configured
                and public_tracking_configured
                and analytics.experiment_count > 0
            ),
            funnel=funnel,
            observed_event_types=observed,
            unobserved_event_types=unobserved,
            blockers=blockers,
        )


product_integration_status_service = ProductIntegrationStatusService()
