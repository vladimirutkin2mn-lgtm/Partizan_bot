from uuid import UUID

from fastapi import FastAPI, HTTPException, status

from app.channel_service import channel_service
from app.execution_service import execution_service, find_growth_play
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.logging import configure_logging
from app.models import ProductProfileStatus
from app.product_intake import product_intake_service
from app.schemas import (
    ChannelDiscoveryResponse,
    ClarificationAnswerRequest,
    ExecutionEditRequest,
    ExecutionPackageView,
    ExecutionPrepareRequest,
    ExecutionRunResponse,
    ExperimentView,
    GrowthPlayApprovalRequest,
    GrowthPlayGenerationResponse,
    GrowthPlayView,
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
    version="0.6.0",
    description="Discovery, experiment design and human-approved execution API for Partizan Bot.",
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


@app.post(
    "/v1/products/{product_id}/channels/discover",
    response_model=ChannelDiscoveryResponse,
    tags=["channels"],
)
async def discover_channels(product_id: UUID) -> ChannelDiscoveryResponse:
    try:
        product = product_intake_service.get_product(product_id)
        icp_result = icp_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Generate ICPs before channel discovery",
        ) from exc
    try:
        return await channel_service.discover(product, icp_result)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get(
    "/v1/products/{product_id}/channels",
    response_model=ChannelDiscoveryResponse,
    tags=["channels"],
)
async def get_channels(product_id: UUID) -> ChannelDiscoveryResponse:
    try:
        return channel_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Channel discovery not found") from exc


@app.post(
    "/v1/products/{product_id}/growth-plays/generate",
    response_model=GrowthPlayGenerationResponse,
    tags=["growth-plays"],
)
async def generate_growth_plays(product_id: UUID) -> GrowthPlayGenerationResponse:
    try:
        product = product_intake_service.get_product(product_id)
        icp_result = icp_service.get(product_id)
        channel_result = channel_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Generate ICPs and discover channels before Growth Plays",
        ) from exc
    try:
        return await growth_play_service.generate(product, icp_result, channel_result)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get(
    "/v1/products/{product_id}/growth-plays",
    response_model=GrowthPlayGenerationResponse,
    tags=["growth-plays"],
)
async def get_growth_plays(product_id: UUID) -> GrowthPlayGenerationResponse:
    try:
        return growth_play_service.get(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Growth Play generation not found") from exc


@app.post(
    "/v1/products/{product_id}/growth-plays/{play_id}/approval",
    response_model=GrowthPlayView,
    tags=["growth-plays"],
)
async def set_growth_play_approval(
    product_id: UUID,
    play_id: UUID,
    payload: GrowthPlayApprovalRequest,
) -> GrowthPlayView:
    try:
        return growth_play_service.set_status(product_id, play_id, payload.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Growth Play not found") from exc


@app.post(
    "/v1/products/{product_id}/growth-plays/{play_id}/execution/prepare",
    response_model=ExecutionPackageView,
    tags=["execution"],
)
async def prepare_execution(
    product_id: UUID,
    play_id: UUID,
    payload: ExecutionPrepareRequest,
) -> ExecutionPackageView:
    try:
        product = product_intake_service.get_product(product_id)
        play = find_growth_play(product_id, play_id)
        return await execution_service.prepare(product, play, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product, play or channel not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get(
    "/v1/execution-packages/{package_id}",
    response_model=ExecutionPackageView,
    tags=["execution"],
)
async def get_execution_package(package_id: UUID) -> ExecutionPackageView:
    try:
        return execution_service.get_package(package_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Execution package not found") from exc


@app.patch(
    "/v1/execution-packages/{package_id}",
    response_model=ExecutionPackageView,
    tags=["execution"],
)
async def edit_execution_package(
    package_id: UUID,
    payload: ExecutionEditRequest,
) -> ExecutionPackageView:
    try:
        return execution_service.edit(package_id, payload.subject, payload.body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Execution package not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/execution-packages/{package_id}/approve",
    response_model=ExecutionPackageView,
    tags=["execution"],
)
async def approve_execution_package(package_id: UUID) -> ExecutionPackageView:
    try:
        return execution_service.approve(package_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Execution package not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/execution-packages/{package_id}/reject",
    response_model=ExecutionPackageView,
    tags=["execution"],
)
async def reject_execution_package(package_id: UUID) -> ExecutionPackageView:
    try:
        return execution_service.reject(package_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Execution package not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post(
    "/v1/execution-packages/{package_id}/run",
    response_model=ExecutionRunResponse,
    tags=["execution"],
)
async def run_execution_package(package_id: UUID) -> ExecutionRunResponse:
    try:
        return await execution_service.run(package_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Execution package not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Execution provider failed") from exc


@app.get("/v1/experiments/{experiment_id}", response_model=ExperimentView, tags=["experiments"])
async def get_experiment(experiment_id: UUID) -> ExperimentView:
    try:
        return execution_service.get_experiment(experiment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Experiment not found") from exc


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
