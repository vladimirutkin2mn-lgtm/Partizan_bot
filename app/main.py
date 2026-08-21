from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text

from app.config import get_settings
from app.customer_routes import router as customer_router
from app.db import get_sync_engine
from app.distribution_play_routes import router as distribution_play_router
from app.distribution_routes import router as distribution_router
from app.growth_balance_funding_policy import enable_checkout_first_growth_balance_funding
from app.growth_balance_rail_routes import router as growth_balance_rail_router
from app.icp_service import icp_service
from app.logging import configure_logging
from app.operator_auth import require_control_plane_operator
from app.product_intake import product_intake_service
from app.schemas import (
    ClarificationAnswerRequest,
    ICPGenerationResponse,
    ProductCreateRequest,
    ProductIntakeResponse,
    ProductProfileView,
)
from app.web_routes import router as web_router

enable_checkout_first_growth_balance_funding()
configure_logging()

app = FastAPI(
    title="Partizan Bot API",
    version="0.8.0",
    description="Autonomous growth discovery, execution, analytics and decision API.",
    dependencies=[Depends(require_control_plane_operator)],
)
app.include_router(web_router)
app.include_router(customer_router)
app.include_router(growth_balance_rail_router)
app.include_router(distribution_router)
app.include_router(distribution_play_router)


@app.get("/health", tags=["system"])
@app.get("/health/live", tags=["system"])
async def health() -> dict[str, str]:
    """Process liveness without depending on third-party providers."""

    return {"status": "ok"}


@app.get("/health/ready", tags=["system"])
def readiness() -> dict[str, str]:
    """Return ready only when the production persistence dependency is reachable."""

    try:
        with get_sync_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "available"}


@app.get("/version", tags=["system"])
def version() -> dict[str, str]:
    """Expose the exact release SHA currently served by this runtime."""

    settings = get_settings()
    return {
        "service": "partizan",
        "api_version": app.version,
        "release_sha": settings.partizan_release_sha,
    }


@app.post(
    "/v1/products",
    response_model=ProductIntakeResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["products"],
)
async def create_product(payload: ProductCreateRequest) -> ProductIntakeResponse:
    return await product_intake_service.create_draft(payload)


@app.get("/v1/products/{product_id}", response_model=ProductProfileView, tags=["products"])
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


@app.get("/v1/products/{product_id}/icps", response_model=ICPGenerationResponse, tags=["icp"])
async def get_icps(product_id: UUID) -> ICPGenerationResponse:
    try:
        return icp_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="ICP generation not found") from exc
