from __future__ import annotations

from urllib.parse import urlparse

from app.customer_channel_schemas import CustomerStartingMoveView
from app.customer_channels import (
    CHANNEL_LABELS,
    SELECTED_ACQUISITION_CHANNEL_KEY,
)
from app.customer_schemas import CustomerFreeOpportunityView, CustomerOpportunityView
from app.distribution_types import DistributionPlatform


class CustomerStartingMoveService:
    def view(self, project: dict) -> CustomerStartingMoveView | None:
        platform = self._selected_platform(project)
        if platform is None:
            return None

        full_research = self._full_research_move(project, platform)
        if full_research is not None:
            return full_research

        preview = self._preview_move(project, platform)
        if preview is not None:
            return preview

        label = CHANNEL_LABELS[platform]
        return CustomerStartingMoveView(
            platform=platform,
            channel_label=label,
            state="NEEDS_RESEARCH",
            source="SELECTED_CHANNEL",
            title=f"Research {label} before taking action",
            rationale=(
                f"You chose {label} as the starting focus, but Partizan does not yet have "
                "a channel-specific opportunity backed by source evidence."
            ),
            recommended_action=(
                f"Continue research for {label} until Partizan has a concrete opportunity. "
                "Do not connect an account or fund a test just to force a move."
            ),
            signal_to_watch="A concrete channel-specific opportunity with source evidence.",
            execution_requirement=(
                "Research only. No execution permission, account connection or acquisition spend "
                "is required to close this evidence gap."
            ),
        )

    def _full_research_move(
        self,
        project: dict,
        platform: DistributionPlatform,
    ) -> CustomerStartingMoveView | None:
        research = project.get("research")
        if not isinstance(research, dict):
            return None
        raw_opportunities = research.get("opportunities")
        if not isinstance(raw_opportunities, list):
            return None

        exact: list[CustomerOpportunityView] = []
        broad: list[CustomerOpportunityView] = []
        for raw in raw_opportunities:
            if not isinstance(raw, dict):
                continue
            try:
                opportunity = CustomerOpportunityView.model_validate(raw)
            except ValueError:
                continue
            if (
                opportunity.surface == "EXECUTION_PLATFORM"
                and str(opportunity.platform or "").upper() == platform.value
            ):
                exact.append(opportunity)
                continue
            if self._opportunity_matches_platform(opportunity, platform):
                broad.append(opportunity)

        opportunity = (exact or broad or [None])[0]
        if opportunity is None:
            return None
        label = CHANNEL_LABELS[platform]
        return CustomerStartingMoveView(
            platform=platform,
            channel_label=label,
            state="READY",
            source="FULL_RESEARCH",
            title=opportunity.title,
            rationale=opportunity.rationale or f"Research identified this {label} opportunity.",
            recommended_action=(
                f"Open this researched {label} opportunity, validate the context, and prepare the "
                "first channel-native test around this exact evidence. Do not publish or spend "
                "until the separate execution controls are explicitly configured."
            ),
            signal_to_watch="The first measurable acquisition signal tied to this exact opportunity.",
            execution_requirement=(
                opportunity.execution_requirement
                or (
                    "Research is not execution permission. "
                    "Channel access and spend remain separately controlled."
                )
            ),
            url=opportunity.url,
            provenance=opportunity.provenance,
        )

    def _preview_move(
        self,
        project: dict,
        platform: DistributionPlatform,
    ) -> CustomerStartingMoveView | None:
        preview = project.get("preview")
        if not isinstance(preview, dict):
            return None
        raw = preview.get("free_opportunity")
        if not isinstance(raw, dict):
            return None
        try:
            opportunity = CustomerFreeOpportunityView.model_validate(raw)
        except ValueError:
            return None
        if not self._opportunity_matches_platform(opportunity, platform):
            return None

        return CustomerStartingMoveView(
            platform=platform,
            channel_label=CHANNEL_LABELS[platform],
            state="READY",
            source="PREVIEW_RESEARCH",
            title=opportunity.title,
            rationale=opportunity.rationale,
            recommended_action=opportunity.recommended_action,
            signal_to_watch=opportunity.signal_to_watch,
            execution_requirement=opportunity.execution_requirement,
            url=opportunity.url,
            provenance=opportunity.provenance,
        )

    @classmethod
    def _opportunity_matches_platform(
        cls,
        opportunity: CustomerFreeOpportunityView | CustomerOpportunityView,
        platform: DistributionPlatform,
    ) -> bool:
        direct = getattr(opportunity, "platform", None)
        if direct and str(direct).upper() == platform.value:
            return True

        urls: list[str] = []
        if opportunity.url is not None:
            urls.append(str(opportunity.url))
        urls.extend(str(item.url) for item in opportunity.provenance if item.url is not None)
        return any(cls._url_matches_platform(url, platform) for url in urls)

    @staticmethod
    def _url_matches_platform(url: str, platform: DistributionPlatform) -> bool:
        try:
            host = (urlparse(url).hostname or "").lower().rstrip(".")
        except ValueError:
            return False
        if platform == DistributionPlatform.REDDIT:
            return host == "reddit.com" or host.endswith(".reddit.com")
        if platform == DistributionPlatform.TELEGRAM:
            return host in {"t.me", "telegram.me"} or host.endswith(".t.me")
        if platform == DistributionPlatform.INSTAGRAM:
            return (
                host == "instagram.com"
                or host.endswith(".instagram.com")
                or host == "facebook.com"
                or host.endswith(".facebook.com")
            )
        if platform == DistributionPlatform.TIKTOK:
            return host == "tiktok.com" or host.endswith(".tiktok.com")
        return False

    @staticmethod
    def _selected_platform(project: dict) -> DistributionPlatform | None:
        raw = project.get(SELECTED_ACQUISITION_CHANNEL_KEY)
        if raw is None:
            return None
        try:
            return DistributionPlatform(str(raw).strip().upper())
        except ValueError:
            return None


customer_starting_move_service = CustomerStartingMoveService()
