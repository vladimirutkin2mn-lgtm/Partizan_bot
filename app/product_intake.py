from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.llm import get_llm_provider
from app.models import ProductProfileStatus
from app.product_agent import ProductAnalysis, ProductIntakeAgent
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import (
    ClarificationAnswerRequest,
    ClarificationQuestionView,
    ProductCreateRequest,
    ProductIntakeResponse,
    ProductProfileView,
)

PRODUCT_INTAKE_NAMESPACE = "product_intake"


@dataclass(slots=True)
class ProductIntakeState:
    product: ProductProfileView
    brief: str
    reference_links: list[str]
    questions: list[ClarificationQuestionView] = field(default_factory=list)
    answers: list[tuple[str, str, str]] = field(default_factory=list)
    answered_fields: set[str] = field(default_factory=set)


class InMemoryProductIntakeService:
    """Product intake service with a local cache backed by RuntimeStateStore.

    The historic class name is intentionally preserved while the migration is staged.
    In memory mode this behaves exactly like the previous service. In database mode,
    cache misses hydrate from durable snapshots so a process restart does not lose the
    confirmed ProductProfile or clarification context.
    """

    def __init__(
        self,
        agent: ProductIntakeAgent,
        store: RuntimeStateStore | None = None,
    ) -> None:
        self._agent = agent
        self._store = store or get_runtime_store()
        self._states: dict[UUID, ProductIntakeState] = {}

    async def create_draft(self, payload: ProductCreateRequest) -> ProductIntakeResponse:
        reference_links = [str(link) for link in payload.reference_links]
        analysis = await self._agent.analyze(
            brief=payload.brief,
            reference_links=reference_links,
        )
        product_id = uuid4()
        questions = self._build_questions(analysis)
        status = (
            ProductProfileStatus.NEEDS_CLARIFICATION
            if questions
            else ProductProfileStatus.DRAFT
        )
        product = self._build_profile(
            product_id=product_id,
            brief=payload.brief,
            reference_links=reference_links,
            analysis=analysis,
            status=status,
        )
        state = ProductIntakeState(
            product=product,
            brief=payload.brief,
            reference_links=reference_links,
            questions=questions,
        )
        self._states[product_id] = state
        self._persist_state(state)
        return self._response(state)

    def get_product(self, product_id: UUID) -> ProductProfileView:
        return self._load_state(product_id).product

    def get_state(self, product_id: UUID) -> ProductIntakeState:
        return self._load_state(product_id)

    async def apply_answer(
        self,
        product_id: UUID,
        payload: ClarificationAnswerRequest,
    ) -> ProductIntakeResponse:
        state = self._load_state(product_id)
        question = next(
            (item for item in state.questions if item.id == payload.question_id),
            None,
        )
        if question is None:
            raise KeyError(payload.question_id)

        state.answers.append((question.field_name, question.question, payload.answer))
        state.answered_fields.add(question.field_name)

        analysis = await self._agent.analyze(
            brief=state.brief,
            reference_links=state.reference_links,
            answers=state.answers,
            answered_fields=state.answered_fields,
        )
        state.questions = self._build_questions(analysis)
        status = (
            ProductProfileStatus.NEEDS_CLARIFICATION
            if state.questions
            else ProductProfileStatus.DRAFT
        )
        state.product = self._build_profile(
            product_id=product_id,
            brief=state.brief,
            reference_links=state.reference_links,
            analysis=analysis,
            status=status,
        )
        self._persist_state(state)
        return self._response(state)

    def confirm(self, product_id: UUID) -> ProductIntakeResponse:
        state = self._load_state(product_id)
        if state.questions:
            raise ValueError("Open clarification questions must be answered first")
        state.product = state.product.model_copy(
            update={"status": ProductProfileStatus.CONFIRMED}
        )
        self._persist_state(state)
        return self._response(state)

    def confirm_preview(
        self,
        product_id: UUID,
        *,
        name: str,
        description: str,
        likely_customer: str,
        market: str,
        goal: str,
        budget: float,
    ) -> ProductIntakeResponse:
        """Confirm the founder-reviewed free-scan understanding.

        The founder's explicit confirmation is authoritative for the fields exposed in
        the preview. Any intake clarifications about those fields are therefore resolved
        by the confirmation rather than being carried into the paid/full research path.
        """
        state = self._load_state(product_id)
        audience = [likely_customer.strip()] if likely_customer.strip() else []
        state.product = state.product.model_copy(
            update={
                "name": name.strip() or state.product.name,
                "description": description.strip() or state.product.description,
                "known_audience": audience or state.product.known_audience,
                "market": market.strip() or state.product.market,
                "goal": goal.strip(),
                "budget": float(budget),
                "status": ProductProfileStatus.CONFIRMED,
            }
        )
        state.questions = []
        self._persist_state(state)
        return self._response(state)

    def reset(self) -> None:
        self._states.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(PRODUCT_INTAKE_NAMESPACE)

    def _load_state(self, product_id: UUID) -> ProductIntakeState:
        cached = self._states.get(product_id)
        if cached is not None:
            return cached
        payload = self._store.get(PRODUCT_INTAKE_NAMESPACE, str(product_id))
        if payload is None:
            raise KeyError(product_id)
        state = ProductIntakeState(
            product=ProductProfileView.model_validate(payload["product"]),
            brief=str(payload["brief"]),
            reference_links=list(payload.get("reference_links", [])),
            questions=[
                ClarificationQuestionView.model_validate(item)
                for item in payload.get("questions", [])
            ],
            answers=[tuple(item) for item in payload.get("answers", [])],
            answered_fields=set(payload.get("answered_fields", [])),
        )
        self._states[product_id] = state
        return state

    def _persist_state(self, state: ProductIntakeState) -> None:
        self._store.put(
            PRODUCT_INTAKE_NAMESPACE,
            str(state.product.id),
            {
                "product": state.product.model_dump(mode="json"),
                "brief": state.brief,
                "reference_links": list(state.reference_links),
                "questions": [item.model_dump(mode="json") for item in state.questions],
                "answers": [list(item) for item in state.answers],
                "answered_fields": sorted(state.answered_fields),
            },
        )

    def _build_questions(
        self,
        analysis: ProductAnalysis,
    ) -> list[ClarificationQuestionView]:
        return [
            ClarificationQuestionView(
                id=uuid4(),
                field_name=item.field_name,
                question=item.question,
                rationale=item.rationale,
                priority=item.priority,
            )
            for item in analysis.clarifications
        ]

    def _build_profile(
        self,
        product_id: UUID,
        brief: str,
        reference_links: list[str],
        analysis: ProductAnalysis,
        status: ProductProfileStatus,
    ) -> ProductProfileView:
        return ProductProfileView(
            id=product_id,
            input_brief=brief,
            name=analysis.name or "Product",
            description=analysis.description or brief,
            problem_or_desire=analysis.problem_or_desire,
            value_proposition=analysis.value_proposition,
            usp=analysis.usp,
            use_cases=analysis.use_cases,
            market=analysis.market,
            language=analysis.language,
            price=analysis.price,
            pricing_model=analysis.pricing_model,
            goal=analysis.goal,
            budget=analysis.budget,
            max_cac=analysis.max_cac,
            allowed_channels=analysis.allowed_channels,
            constraints=analysis.constraints,
            known_audience=analysis.known_audience,
            known_competitors=analysis.known_competitors,
            reference_links=reference_links,
            assumptions=analysis.assumptions,
            contradictions=analysis.contradictions,
            status=status,
        )

    def _response(self, state: ProductIntakeState) -> ProductIntakeResponse:
        if state.product.status == ProductProfileStatus.CONFIRMED:
            next_action = "start_growth"
        elif state.questions:
            next_action = "answer_clarifications"
        else:
            next_action = "confirm"
        return ProductIntakeResponse(
            product=state.product,
            clarifications=state.questions,
            next_action=next_action,
        )


product_intake_service = InMemoryProductIntakeService(
    ProductIntakeAgent(get_llm_provider())
)
