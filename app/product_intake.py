from dataclasses import dataclass
from uuid import UUID, uuid4

from app.models import ProductProfileStatus
from app.schemas import (
    ClarificationAnswerRequest,
    ClarificationQuestionView,
    ProductCreateRequest,
    ProductIntakeResponse,
    ProductProfileView,
)


@dataclass(frozen=True, slots=True)
class ClarificationRule:
    field_name: str
    question: str
    rationale: str


RULES = (
    ClarificationRule(
        "value_proposition",
        "В чём главное УТП продукта?",
        "УТП влияет на позиционирование, hooks и сравнение с альтернативами.",
    ),
    ClarificationRule(
        "market",
        "На какой рынок или географию целимся?",
        "Рынок меняет язык, доступные каналы, конкурентов и стоимость привлечения.",
    ),
    ClarificationRule(
        "goal",
        "Какой измеримый маркетинговый результат нужен?",
        "Без цели нельзя ранжировать Growth Plays по ожидаемому результату.",
    ),
)


class InMemoryProductIntakeService:
    def __init__(self) -> None:
        self._products: dict[UUID, ProductProfileView] = {}
        self._questions: dict[UUID, list[ClarificationQuestionView]] = {}

    def create_draft(self, payload: ProductCreateRequest) -> ProductIntakeResponse:
        product_id = uuid4()
        values = payload.model_dump(mode="json")
        values["reference_links"] = [str(link) for link in payload.reference_links]
        missing = [rule for rule in RULES if not values.get(rule.field_name)][:3]
        status = (
            ProductProfileStatus.NEEDS_CLARIFICATION
            if missing
            else ProductProfileStatus.CONFIRMED
        )
        product = ProductProfileView(
            id=product_id,
            assumptions=[],
            status=status,
            **values,
        )
        questions = [
            ClarificationQuestionView(
                id=uuid4(),
                field_name=rule.field_name,
                question=rule.question,
                rationale=rule.rationale,
            )
            for rule in missing
        ]
        self._products[product_id] = product
        self._questions[product_id] = questions
        return ProductIntakeResponse(product=product, clarifications=questions)

    def get_product(self, product_id: UUID) -> ProductProfileView:
        return self._products[product_id]

    def apply_answer(
        self,
        product_id: UUID,
        payload: ClarificationAnswerRequest,
    ) -> ProductIntakeResponse:
        product = self._products[product_id]
        questions = self._questions[product_id]
        question = next((item for item in questions if item.id == payload.question_id), None)
        if question is None:
            raise KeyError(payload.question_id)

        updated = product.model_copy(update={question.field_name: payload.answer})
        remaining = [item for item in questions if item.id != payload.question_id]
        updated = updated.model_copy(
            update={
                "status": (
                    ProductProfileStatus.NEEDS_CLARIFICATION
                    if remaining
                    else ProductProfileStatus.CONFIRMED
                )
            }
        )
        self._products[product_id] = updated
        self._questions[product_id] = remaining
        return ProductIntakeResponse(product=updated, clarifications=remaining)


product_intake_service = InMemoryProductIntakeService()
