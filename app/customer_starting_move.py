from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from app.broad_research import BroadResearchService, broad_research_service
from app.customer_channel_schemas import CustomerStartingMoveView
from app.customer_channels import (
    CHANNEL_LABELS,
    SELECTED_ACQUISITION_CHANNEL_KEY,
)
from app.customer_schemas import (
    CustomerFreeOpportunityView,
    CustomerOpportunityView,
    CustomerResearchEvidenceView,
)
from app.distribution_types import DistributionPlatform
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

CUSTOMER_STARTING_MOVE_RESEARCH_NAMESPACE = "customer_starting_move_research"


class CustomerStartingMoveService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        broad_research: BroadResearchService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._broad_research = broad_research or broad_research_service

    def view(self, project: dict) -> CustomerStartingMoveView | None:
        platform = self._selected_platform(project)
        if platform is None:
            return None

        full_research = self._full_research_move(project, platform)
        if full_research is not None:
            return full_research

        scoped_research = self._scoped_research_move(project, platform)
        if scoped_research is not None:
            return scoped_research

        preview = self._preview_move(project, platform)
        if preview is not None:
            return preview

        return self._needs_research_move(project, platform)

    async def research(self, project: dict) -> CustomerStartingMoveView:
        platform = self._selected_platform(project)
        if platform is None:
            raise ValueError("Choose a starting channel before requesting channel research.")

        current = self.view(project)
        if current is not None and current.state == "READY":
            return current

        product_id_raw = project.get("product_id")
        if not product_id_raw:
            raise ValueError("Review the product understanding before researching this channel.")
        product_id = UUID(str(product_id_raw))
        try:
            product = product_intake_service.get_product(product_id)
        except KeyError as exc:
            raise ValueError(
                "Review the product understanding before researching this channel."
            ) from exc
        try:
            icp_result = icp_service.get(product_id)
        except KeyError:
            icp_result = await icp_service.generate(product)

        opportunity = await self._broad_research.discover_platform_preview(
            product,
            icp_result,
            platform,
        )
        key = self._research_key(project, platform)
        searched_at = datetime.now(UTC).isoformat()
        if opportunity is None:
            self._store.put(
                CUSTOMER_STARTING_MOVE_RESEARCH_NAMESPACE,
                key,
                {
                    "status": "NO_MATCH",
                    "platform": platform.value,
                    "searched_at": searched_at,
                },
            )
            return self._needs_research_move(project, platform, searched=True)

        label = CHANNEL_LABELS[platform]
        move = CustomerStartingMoveView(
            platform=platform,
            channel_label=label,
            state="READY",
            source="CHANNEL_RESEARCH",
            title=opportunity.title,
            rationale=opportunity.rationale,
            recommended_action=(
                f"Open this researched {label} opportunity, verify the audience context, "
                "and prepare the first channel-native test around this exact evidence. "
                "Do not publish or spend until the separate execution controls are configured."
            ),
            signal_to_watch=(
                "The first measurable acquisition signal tied to this exact researched opportunity."
            ),
            execution_requirement=opportunity.execution_requirement,
            url=opportunity.url,
            provenance=[
                CustomerResearchEvidenceView(
                    query=item.query,
                    title=item.title,
                    url=item.url,
                    snippet=item.snippet,
                )
                for item in opportunity.provenance
            ],
        )
        self._store.put(
            CUSTOMER_STARTING_MOVE_RESEARCH_NAMESPACE,
            key,
            {
                "status": "FOUND",
                "platform": platform.value,
                "searched_at": searched_at,
                "move": move.model_dump(mode="json"),
            },
        )
        return move

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CUSTOMER_STARTING_MOVE_RESEARCH_NAMESPACE)

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
                    "Research is not execution permission. Channel access and spend remain "
                    "separately controlled."
                )
            ),
            url=opportunity.url,
            provenance=opportunity.provenance,
        )

    def _scoped_research_move(
        self,
        project: dict,
        platform: DistributionPlatform,
    ) -> CustomerStartingMoveView | None:
        payload = self._store.get(
            CUSTOMER_STARTING_MOVE_RESEARCH_NAMESPACE,
            self._research_key(project, platform),
        )
        if not isinstance(payload, dict) or payload.get("status") != "FOUND":
            return None
        raw_move = payload.get("move")
        if not isinstance(raw_move, dict):
            return None
        try:
            move = CustomerStartingMoveView.model_validate(raw_move)
        except ValueError:
            return None
        if move.platform != platform or move.state != "READY":
            return None
        return move

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

    def _needs_research_move(
        self,
        project: dict,
        platform: DistributionPlatform,
        *,
        searched: bool = False,
    ) -> CustomerStartingMoveView:
        if not searched:
            payload = self._store.get(
                CUSTOMER_STARTING_MOVE_RESEARCH_NAMESPACE,
                self._research_key(project, platform),
            )
            searched = isinstance(payload, dict) and payload.get("status") == "NO_MATCH"
        label = CHANNEL_LABELS[platform]
        if searched:
            title = f"No strong {label} opportunity found yet"
            rationale = (
                f"Partizan searched {label} for this product and audience, but no source evidence "
                "cleared the bar for a concrete first move."
            )
            recommended_action = (
                f"Keep {label} as the research focus and retry when there is stronger public "
                "evidence. Do not connect an account or fund a test just to force a move."
            )
        else:
            title = f"Research {label} before taking action"
            rationale = (
                f"You chose {label} as the starting focus, but Partizan does not yet have "
                "a channel-specific opportunity backed by source evidence."
            )
            recommended_action = (
                f"Research {label} until Partizan has a concrete opportunity. "
                "Do not connect an account or fund a test just to force a move."
            )
        return CustomerStartingMoveView(
            platform=platform,
            channel_label=label,
            state="NEEDS_RESEARCH",
            source="SELECTED_CHANNEL",
            title=title,
            rationale=rationale,
            recommended_action=recommended_action,
            signal_to_watch="A concrete channel-specific opportunity with source evidence.",
            execution_requirement=(
                "Research only. No execution permission, account connection or acquisition spend "
                "is required to close this evidence gap."
            ),
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

    @staticmethod
    def _research_key(project: dict, platform: DistributionPlatform) -> str:
        return f"{project['id']}:{platform.value}"


customer_starting_move_service = CustomerStartingMoveService()
