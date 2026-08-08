from dataclasses import dataclass, field
from uuid import UUID, uuid4

from app.llm import get_llm_provider
from app.models import ProductProfileStatus
from app.product_agent import ProductAnalysis, ProductIntakeAgent
from app.schemas import (
    ClarificationAnswerRequest,
    ClarificationQuestionView,
    ProductCreateRequest,
    ProductIntakeResponse,
    ProductProfileView,
)


@dataclass(slots=True)
class ProductIntakeState:
    product: ProductProfileView
    brief: str
    reference_links: list[str]
    questions: list[ClarificationQuestionView] = field(default_factory=list)
    answers: list[tuple[str, str, str]] = field(default_factory=list)
    answered_fields: set[str] = field(default_factory=set)


class InMemoryProductIntakeService:
    def __init__(self, agent: ProductIntakeAgent) -> None:
        self._agent = agent
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
        self._states[product_id] = ProductIntakeState(
            product=product,
            brief=payload.brief,
            reference_links=reference_links,
            questions=questions,
        )
        return self._response(self._states[product_id])

    def get_product(self, product_id: UUID) -> ProductProfileView:
        return self._states[product_id].product

    def get_state(self, product_id: UUID) -> ProductIntakeState:
        return self._states[product_id]

    async def apply_answer(
        self,
        product_id: UUID,
        payload: ClarificationAnswerRequest,
    ) -> ProductIntakeResponse:
        state = self._states[product_id]
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
        return self._response(state)

    def confirm(self, product_id: UUID) -> ProductIntakeResponse:
        state = self._states[product_id]
        if state.questions:
            raise ValueError("Open clarification questions must be answered first")
        state.product = state.product.model_copy(
            update={"status": ProductProfileStatus.CONFIRMED}
        )
        return self._response(state)

    def reset(self) -> None:
        self._states.clear()

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
