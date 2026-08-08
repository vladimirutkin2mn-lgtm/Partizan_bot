from uuid import UUID

from fastapi import FastAPI, HTTPException, status

from app.logging import configure_logging
from app.product_intake import product_intake_service
from app.schemas import (
    ClarificationAnswerRequest,
    MockWorkflowResponse,
    ProductCreateRequest,
    ProductIntakeResponse,
    WorkflowStageView,
)
from app.workflow import build_mock_growth_workflow

configure_logging()

app = FastAPI(
    title="Partizan Bot API",
    version="0.1.0",
    description="Foundation API for the Partizan Bot growth engine.",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/v1/products",
    response_model=ProductIntakeResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["products"],
)
async def create_product(payload: ProductCreateRequest) -> ProductIntakeResponse:
    return product_intake_service.create_draft(payload)


@app.post(
    "/v1/products/{product_id}/clarifications",
    response_model=ProductIntakeResponse,
    tags=["products"],
)
async def answer_clarification(
    product_id: UUID,
    payload: ClarificationAnswerRequest,
) -> ProductIntakeResponse:
    try:
        return product_intake_service.apply_answer(product_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product or question not found") from exc


@app.post(
    "/v1/products/{product_id}/mock-workflow",
    response_model=MockWorkflowResponse,
    tags=["products"],
)
async def start_mock_workflow(product_id: UUID) -> MockWorkflowResponse:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc

    stages = build_mock_growth_workflow(product_id)
    return MockWorkflowResponse(
        stages=[WorkflowStageView(name=stage.name, status=stage.status) for stage in stages]
    )
