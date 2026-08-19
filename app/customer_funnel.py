from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.audience_intelligence_service import audience_intelligence_service
from app.config import get_settings
from app.customer_schemas import (
    CustomerClarificationAnswerRequest,
    CustomerClarificationView,
    CustomerDirectionView,
    CustomerICPView,
    CustomerOpportunityView,
    CustomerPreviewRequest,
    CustomerPreviewResponse,
    CustomerProjectView,
    CustomerResearchResponse,
    MaskedOpportunityView,
)
from app.icp_service import icp_service
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ClarificationAnswerRequest, ProductCreateRequest

CUSTOMER_PROJECT_NAMESPACE = "customer_acquisition_projects"
CUSTOMER_TOKEN_HEADER = "X-Partizan-Customer-Token"


class CustomerProjectNotFoundError(KeyError):
    pass


class CustomerProjectAccessError(PermissionError):
    pass


class CustomerPaymentRequiredError(PermissionError):
    pass


class CustomerFunnelService:
    """Public customer funnel with a zero-token preview and paid deep research.

    The free preview is deterministic and never calls the LLM/search providers. The
    expensive Product Intake -> ICP -> Distribution chain becomes available after
    either the $49 Acquisition Plan is purchased or Growth Balance is actually funded.
    """

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    def create_preview(self, payload: CustomerPreviewRequest) -> CustomerPreviewResponse:
        project_id = uuid4()
        customer_token = secrets.token_urlsafe(32)
        directions = self._directions(payload)
        scope_estimate = self._scope_estimate(payload)
        settings = get_settings()
        project = {
            "id": str(project_id),
            "customer_token_hash": self._hash_token(customer_token),
            "brief": payload.brief,
            "website_url": str(payload.website_url) if payload.website_url else None,
            "market": payload.market,
            "goal": payload.goal,
            "budget_usd": payload.budget_usd,
            "status": "PREVIEW",
            "launch_unlocked": False,
            "research_state": "NOT_STARTED",
            "product_id": None,
            "stripe_checkout_session_id": None,
            "stripe_customer_id": None,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "preview": {
                "channel_count": len(directions),
                "opportunity_scope_estimate": scope_estimate,
                "fastest_signal": directions[0].name,
                "directions": [item.model_dump(mode="json") for item in directions],
            },
        }
        self._store.put(CUSTOMER_PROJECT_NAMESPACE, str(project_id), project)
        return CustomerPreviewResponse(
            project_id=project_id,
            customer_token=customer_token,
            channel_count=len(directions),
            opportunity_scope_estimate=scope_estimate,
            fastest_signal=directions[0].name,
            directions=directions,
            masked_opportunities=self._masked_opportunities(directions),
            launch_price_usd=settings.partizan_launch_price_usd,
            managed_spend_fee_pct=settings.partizan_managed_spend_fee_pct,
        )

    def get_project(self, project_id: UUID, customer_token: str) -> CustomerProjectView:
        project = self._authorized_project(project_id, customer_token)
        return self._project_view(project)

    def get_project_payload(self, project_id: UUID, customer_token: str) -> dict:
        return self._authorized_project(project_id, customer_token)

    def mark_checkout_pending(self, project_id: UUID, customer_token: str, session_id: str) -> None:
        project = self._authorized_project(project_id, customer_token)
        if project["launch_unlocked"]:
            return
        project["stripe_checkout_session_id"] = session_id
        project["status"] = "CHECKOUT_PENDING"
        self._persist(project)

    def unlock_launch(
        self,
        project_id: UUID,
        *,
        stripe_checkout_session_id: str,
        stripe_customer_id: str | None,
    ) -> bool:
        project = self._load(project_id)
        if project is None:
            return False
        expected_session = project.get("stripe_checkout_session_id")
        if expected_session != stripe_checkout_session_id:
            return False
        project["stripe_checkout_session_id"] = stripe_checkout_session_id
        project["stripe_customer_id"] = stripe_customer_id
        project["launch_unlocked"] = True
        project["status"] = "UNLOCKED"
        project["launch_entitlement_source"] = "ACQUISITION_PLAN"
        project["launch_unlocked_at"] = datetime.now(UTC).isoformat()
        self._persist(project)
        return True

    def unlock_from_growth_balance(
        self,
        project_id: UUID,
        *,
        stripe_customer_id: str | None = None,
    ) -> bool:
        project = self._load(project_id)
        if project is None:
            return False
        if stripe_customer_id:
            project["stripe_customer_id"] = stripe_customer_id
        if not project.get("launch_unlocked"):
            project["launch_unlocked"] = True
            project["launch_entitlement_source"] = "GROWTH_BALANCE"
            project["launch_unlocked_at"] = datetime.now(UTC).isoformat()
            if project.get("status") in {"PREVIEW", "CHECKOUT_PENDING"}:
                project["status"] = "UNLOCKED"
        self._persist(project)
        return True

    async def start_deep_research(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> CustomerResearchResponse:
        project = self._authorized_project(project_id, customer_token)
        self._require_launch_entitlement(project)
        if project.get("research_state") == "READY":
            return self._cached_research(project)

        product_id_raw = project.get("product_id")
        if product_id_raw:
            product_id = UUID(str(product_id_raw))
            state = product_intake_service.get_state(product_id)
            if state.questions:
                return self._needs_input(project, state.product.id, state.questions)
            return await self._finish_research(project, state.product.id)

        website_url = str(project.get("website_url") or "").strip()
        enriched_brief_parts = [
            project["brief"],
            f"Market: {project['market']}",
            f"Test budget: ${project['budget_usd']}",
            f"Business goal: {project['goal']}",
        ]
        if website_url:
            enriched_brief_parts.append(f"Website: {website_url}")
        enriched_brief = "\n\n".join(enriched_brief_parts)
        intake = await product_intake_service.create_draft(
            ProductCreateRequest(
                brief=enriched_brief,
                reference_links=[website_url] if website_url else [],
            )
        )
        project["product_id"] = str(intake.product.id)
        if intake.clarifications:
            project["research_state"] = "NEEDS_INPUT"
            self._persist(project)
            return self._needs_input(project, intake.product.id, intake.clarifications)

        product_intake_service.confirm(intake.product.id)
        return await self._finish_research(project, intake.product.id)

    async def answer_clarification(
        self,
        project_id: UUID,
        customer_token: str,
        payload: CustomerClarificationAnswerRequest,
    ) -> CustomerResearchResponse:
        project = self._authorized_project(project_id, customer_token)
        self._require_launch_entitlement(project)
        product_id_raw = project.get("product_id")
        if not product_id_raw:
            raise CustomerProjectNotFoundError("Deep research has not started")
        product_id = UUID(str(product_id_raw))
        intake = await product_intake_service.apply_answer(
            product_id,
            ClarificationAnswerRequest(
                question_id=payload.question_id,
                answer=payload.answer,
            ),
        )
        if intake.clarifications:
            project["research_state"] = "NEEDS_INPUT"
            self._persist(project)
            return self._needs_input(project, product_id, intake.clarifications)

        product_intake_service.confirm(product_id)
        return await self._finish_research(project, product_id)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(CUSTOMER_PROJECT_NAMESPACE)

    async def _finish_research(self, project: dict, product_id: UUID) -> CustomerResearchResponse:
        product = product_intake_service.get_product(product_id)
        try:
            icp_result = icp_service.get(product_id)
        except KeyError:
            icp_result = await icp_service.generate(product)
        try:
            distribution = audience_intelligence_service.get(product_id)
        except KeyError:
            distribution = await audience_intelligence_service.discover(product, icp_result)

        icps = [
            CustomerICPView(
                title=item.title,
                description=item.description,
                score=item.score,
                message_hook=item.message_hook,
            )
            for item in icp_result.icps[:5]
        ]
        opportunities = [
            CustomerOpportunityView(
                platform=item.platform.value,
                kind=item.kind.value,
                title=item.title,
                url=item.url,
                rationale=item.rationale,
                relevance_score=item.relevance_score,
            )
            for item in distribution.opportunities[:20]
        ]
        project["research_state"] = "READY"
        project["status"] = "RESEARCH_READY"
        project["research"] = {
            "icps": [item.model_dump(mode="json") for item in icps],
            "opportunities": [item.model_dump(mode="json") for item in opportunities],
        }
        self._persist(project)
        return CustomerResearchResponse(
            project_id=UUID(project["id"]),
            state="READY",
            message="Acquisition research is ready.",
            product_id=product_id,
            icps=icps,
            opportunities=opportunities,
        )

    def _cached_research(self, project: dict) -> CustomerResearchResponse:
        research = project.get("research", {})
        return CustomerResearchResponse(
            project_id=UUID(project["id"]),
            state="READY",
            message="Acquisition research is ready.",
            product_id=UUID(project["product_id"]),
            icps=[CustomerICPView.model_validate(item) for item in research.get("icps", [])],
            opportunities=[
                CustomerOpportunityView.model_validate(item)
                for item in research.get("opportunities", [])
            ],
        )

    def _needs_input(self, project: dict, product_id: UUID, questions: list) -> CustomerResearchResponse:
        clarifications = [
            CustomerClarificationView(
                question_id=item.id,
                question=item.question,
                rationale=item.rationale,
            )
            for item in questions
        ]
        return CustomerResearchResponse(
            project_id=UUID(project["id"]),
            state="NEEDS_INPUT",
            message="One detail is needed before deep research can continue.",
            product_id=product_id,
            clarifications=clarifications,
        )

    def _authorized_project(self, project_id: UUID, customer_token: str) -> dict:
        project = self._load(project_id)
        if project is None:
            raise CustomerProjectNotFoundError(project_id)
        expected = str(project.get("customer_token_hash", ""))
        actual = self._hash_token(customer_token)
        if not expected or not hmac.compare_digest(expected, actual):
            raise CustomerProjectAccessError(project_id)
        return project

    def _load(self, project_id: UUID) -> dict | None:
        return self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))

    def _persist(self, project: dict) -> None:
        project["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(CUSTOMER_PROJECT_NAMESPACE, project["id"], project)

    def _project_view(self, project: dict) -> CustomerProjectView:
        settings = get_settings()
        return CustomerProjectView(
            project_id=UUID(project["id"]),
            status=project["status"],
            brief=project["brief"],
            website_url=project.get("website_url"),
            market=project["market"],
            goal=project["goal"],
            budget_usd=int(project["budget_usd"]),
            launch_unlocked=bool(project["launch_unlocked"]),
            research_state=project["research_state"],
            product_id=UUID(project["product_id"]) if project.get("product_id") else None,
            launch_price_usd=settings.partizan_launch_price_usd,
            managed_spend_fee_pct=settings.partizan_managed_spend_fee_pct,
        )

    @staticmethod
    def _require_launch_entitlement(project: dict) -> None:
        if not project.get("launch_unlocked"):
            raise CustomerPaymentRequiredError(project["id"])

    @staticmethod
    def _hash_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _scope_estimate(payload: CustomerPreviewRequest) -> int:
        seed = f"{payload.brief}|{payload.market}|{payload.goal}".encode()
        return 18 + int(hashlib.sha256(seed).hexdigest()[:4], 16) % 25

    @staticmethod
    def _directions(payload: CustomerPreviewRequest) -> list[CustomerDirectionView]:
        text = f"{payload.brief} {payload.goal}".lower()
        b2b = any(
            token in text
            for token in ("b2b", "saas", "business", "company", "companies", "founder", "teams")
        )
        commerce = any(
            token in text
            for token in ("shop", "store", "ecommerce", "e-commerce", "fashion", "beauty", "consumer")
        )
        if b2b:
            return [
                CustomerDirectionView(
                    name="Direct outreach",
                    potential="HIGH",
                    rationale="The buyer can often be identified and contacted directly.",
                ),
                CustomerDirectionView(
                    name="Niche communities",
                    potential="HIGH",
                    rationale="High-intent professional conversations can validate the offer quickly.",
                ),
                CustomerDirectionView(
                    name="Expert / creator partnerships",
                    potential="MEDIUM",
                    rationale="Trusted niche voices can compress the path to credibility.",
                ),
                CustomerDirectionView(
                    name="Paid acquisition",
                    potential="MEDIUM",
                    rationale="Useful once the message and audience hypothesis are sharp enough to test.",
                ),
            ]
        if commerce:
            return [
                CustomerDirectionView(
                    name="Creators",
                    potential="HIGH",
                    rationale="Product demonstration and social proof can create a fast buying signal.",
                ),
                CustomerDirectionView(
                    name="Paid acquisition",
                    potential="HIGH",
                    rationale="Visual offers can be tested against multiple audiences with bounded spend.",
                ),
                CustomerDirectionView(
                    name="Niche communities",
                    potential="MEDIUM",
                    rationale="Communities can reveal objections and high-intent product conversations.",
                ),
                CustomerDirectionView(
                    name="Partnerships",
                    potential="MEDIUM",
                    rationale="Adjacent brands and publishers can provide concentrated distribution.",
                ),
            ]
        return [
            CustomerDirectionView(
                name="Niche communities",
                potential="HIGH",
                rationale="High-intent conversations are usually the fastest low-cost signal.",
            ),
            CustomerDirectionView(
                name="Creators",
                potential="HIGH",
                rationale="Relevant creators can provide trust and concentrated audience access.",
            ),
            CustomerDirectionView(
                name="Paid acquisition",
                potential="MEDIUM",
                rationale="Bounded paid tests can validate audience and message combinations.",
            ),
            CustomerDirectionView(
                name="Direct outreach",
                potential="MEDIUM",
                rationale="Direct conversations can validate demand before scaling spend.",
            ),
        ]

    @staticmethod
    def _masked_opportunities(
        directions: list[CustomerDirectionView],
    ) -> list[MaskedOpportunityView]:
        masks = {
            "Niche communities": "Community ••••••••",
            "Creators": "Creator @••••••••",
            "Expert / creator partnerships": "Expert @••••••••",
            "Paid acquisition": "Audience ••••••••",
            "Direct outreach": "Prospect cluster ••••••••",
            "Partnerships": "Partner ••••••••",
        }
        return [
            MaskedOpportunityView(category=item.name, label=masks.get(item.name, "Opportunity ••••••••"))
            for item in directions[:4]
        ]


customer_funnel_service = CustomerFunnelService()
