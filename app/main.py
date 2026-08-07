from uuid import UUID

from fastapi import FastAPI, HTTPException, status

from app.icp_service import icp_service
from app.logging import configure_logging
from app.models import ProductProfileStatus
from app.product_intake import product_intake_service
from app.schemas import (
    ClarificationAnswerRequest,
    ICPGenerationResponse,
    MockWorkflowResponse,
    ProductCreateRequest,
    ProductIntakeResponse,
    ProductProfileView,
    WorkflowStageView,
)
from app.workflow import build_mock_growth_workflow

configure_logging()

app = FastAPI(
    title="Partizan Bot API",
    version="0.3.0",
    description="Product intake, ICP discovery and growth-engine API for Partizan Bot.",
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
    return await product_intake_service.create_draft(payload)


@app.get(
    "/v1/products/{product_id}",
    response_model=ProductProfileView,
    tags=["products"],
)
async def get_product(product_id: UUID) -> ProductProfileView:
    try:
        return product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc


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
        return await product_intake_service.apply_answer(product_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product or question not found") from exc


@app.post(
    "/v1/products/{product_id}/confirm",
    response_model=ProductIntakeResponse,
    tags=["products"],
)
async def confirm_product(product_id: UUID) -> ProductIntakeResponse:
    try:
        return product_intake_service.confirm(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/products/{product_id}/icps/generate",
    response_model=ICPGenerationResponse,
    tags=["icp"],
)
async def generate_icps(product_id: UUID) -> ICPGenerationResponse:
    try:
        product = product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc

    try:
        return await icp_service.generate(product)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    "/v1/products/{product_id}/icps",
    response_model=ICPGenerationResponse,
    tags=["icp"],
)
async def get_icps(product_id: UUID) -> ICPGenerationResponse:
    try:
        return icp_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ICP generation not found") from exc


@app.post(
    "/v1/products/{product_id}/mock-workflow",
    response_model=MockWorkflowResponse,
    tags=["products"],
)
async def start_mock_workflow(product_id: UUID) -> MockWorkflowResponse:
    try:
        product = product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc

    if product.status != ProductProfileStatus.CONFIRMED:
        raise HTTPException(
            status_code=409,
            detail="ProductProfile must be CONFIRMED before growth workflow starts",
        )

    stages = build_mock_growth_workflow(product_id)
    return MockWorkflowResponse(
        stages=[WorkflowStageView(name=stage.name, status=stage.status) for stage in stages]
    )
