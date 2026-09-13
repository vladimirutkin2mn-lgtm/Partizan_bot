from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.action_drafting import (
    ACTION_DRAFT_SYSTEM_PROMPT,
    DistributionContentDraft,
    SelectedActionTarget,
)
from app.customer_channel_schemas import (
    CustomerStartingMoveDraftView,
    CustomerStartingMoveView,
)
from app.customer_starting_move import customer_starting_move_service
from app.llm import LLMMessage, LLMProvider, get_llm_provider
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ProductProfileView

CUSTOMER_STARTING_MOVE_DRAFT_NAMESPACE = "customer_starting_move_draft"

REVIEW_ONLY_DRAFT_PROMPT = f"""{ACTION_DRAFT_SYSTEM_PROMPT}

This request is a REVIEW_ONLY customer test draft, not an executable DistributionAction.
The customer has selected a research focus, not granted publishing, account, or spend permission.
Ground the draft in the supplied public source evidence. Do not invent platform permissions or results.
Do not add a direct product link. Do not imply that the draft has been approved, scheduled, or published.
The draft may be a platform-native contribution concept or creative brief that a human can review first.
"""


class CustomerStartingMoveDraftComposer:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self._provider = provider

    async def compose(
        self,
        *,
        product: ProductProfileView,
        move: CustomerStartingMoveView,
        target: SelectedActionTarget,
    ) -> DistributionContentDraft:
        if self._provider is None:
            return self._mock_draft(product=product, move=move, target=target)
        return await self._provider.parse(
            messages=[
                LLMMessage(role="system", content=REVIEW_ONLY_DRAFT_PROMPT),
                LLMMessage(
                    role="user",
                    content=(
                        f"Product: {product.model_dump(mode='json')}\n"
                        f"Selected starting move: {move.model_dump(mode='json')}\n"
                        f"Evidence target: {target}\n"
                        "Prepare one concise first-test draft for review. Keep claims factual and "
                        "source-bound. Publishing and spend remain outside this task."
                    ),
                ),
            ],
            response_model=DistributionContentDraft,
        )

    @staticmethod
    def _mock_draft(
        *,
        product: ProductProfileView,
        move: CustomerStartingMoveView,
        target: SelectedActionTarget,
    ) -> DistributionContentDraft:
        product_value = product.value_proposition or product.description
        problem = product.problem_or_desire or product.description
        return DistributionContentDraft(
            title=f"First {move.channel_label} test for {product.name}"[:300],
            context_text=target.context_text[:8000],
            content_text=(
                f"Review draft: start from the audience problem visible in “{move.title}”. "
                f"Offer one useful perspective on {problem}. If the product is relevant to the "
                f"conversation, introduce {product.name} transparently as the product being tested: "
                f"{product_value}. Keep the contribution useful without requiring a product link, "
                "and use the source context to shape the exact channel-native wording."
            )[:12000],
            rationale=(
                "Evidence-backed first-test draft for human review only; it does not create or "
                "approve an execution action."
            ),
        )


class CustomerStartingMoveDraftService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        composer: CustomerStartingMoveDraftComposer | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._composer = composer or CustomerStartingMoveDraftComposer(get_llm_provider())

    def view(self, project: dict) -> CustomerStartingMoveDraftView | None:
        move = customer_starting_move_service.view(project)
        if move is None or move.state != "READY":
            return None
        source_url = self._source_url(move)
        if source_url is None:
            return None
        payload = self._store.get(
            CUSTOMER_STARTING_MOVE_DRAFT_NAMESPACE,
            self._draft_key(project, move),
        )
        if not isinstance(payload, dict):
            return None
        try:
            draft = CustomerStartingMoveDraftView.model_validate(payload)
        except ValueError:
            return None
        if (
            draft.platform != move.platform
            or str(draft.source_url) != source_url
            or draft.source_title != move.title
        ):
            return None
        return draft

    async def prepare(self, project: dict) -> CustomerStartingMoveDraftView:
        move = customer_starting_move_service.view(project)
        if move is None:
            raise ValueError("Choose a starting channel before preparing a test draft.")
        if move.state != "READY":
            raise ValueError("Research the selected channel before preparing a test draft.")

        source_url = self._source_url(move)
        if source_url is None:
            raise ValueError("A concrete source URL is required before preparing a test draft.")

        product_id_raw = project.get("product_id")
        if not product_id_raw:
            raise ValueError("Review the product understanding before preparing a test draft.")
        product_id = UUID(str(product_id_raw))
        try:
            product = product_intake_service.get_product(product_id)
        except KeyError as exc:
            raise ValueError(
                "Review the product understanding before preparing a test draft."
            ) from exc

        target = SelectedActionTarget(
            url=source_url,
            context_text=self._context(move),
            source=f"customer_starting_move:{move.source.lower()}",
        )
        content = await self._composer.compose(product=product, move=move, target=target)
        draft = CustomerStartingMoveDraftView(
            project_id=UUID(str(project["id"])),
            platform=move.platform,
            channel_label=move.channel_label,
            source_title=move.title,
            source_url=source_url,
            title=content.title,
            context_text=content.context_text,
            content_text=content.content_text,
            rationale=content.rationale,
            signal_to_watch=move.signal_to_watch,
            execution_allowed=False,
            execution_requirement=(
                "Review only. Publishing, account access, approval and acquisition spend remain "
                "separately controlled."
            ),
            provenance=move.provenance,
            created_at=datetime.now(UTC),
        )
        self._store.put(
            CUSTOMER_STARTING_MOVE_DRAFT_NAMESPACE,
            self._draft_key(project, move),
            draft.model_dump(mode="json"),
        )
        return draft

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CUSTOMER_STARTING_MOVE_DRAFT_NAMESPACE)

    @staticmethod
    def _context(move: CustomerStartingMoveView) -> str:
        evidence = [
            " — ".join(part for part in (item.title, item.snippet) if part)
            for item in move.provenance[:3]
        ]
        parts = [
            f"Opportunity: {move.title}",
            f"Platform: {move.channel_label}",
            move.rationale,
            *evidence,
        ]
        return "\n".join(part for part in parts if part)[:8000]

    @staticmethod
    def _source_url(move: CustomerStartingMoveView) -> str | None:
        candidates = []
        if move.url is not None:
            candidates.append(str(move.url))
        candidates.extend(str(item.url) for item in move.provenance if item.url is not None)
        return next(
            (
                url
                for url in candidates
                if customer_starting_move_service._url_matches_platform(url, move.platform)
            ),
            None,
        )

    @staticmethod
    def _draft_key(project: dict, move: CustomerStartingMoveView) -> str:
        return f"{project['id']}:{move.platform.value}"


customer_starting_move_draft_service = CustomerStartingMoveDraftService()
