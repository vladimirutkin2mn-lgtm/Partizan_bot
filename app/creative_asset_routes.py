from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.creative_assets import (
    CreativeAssetRegisterRequest,
    CreativeAssetView,
    CreativeBriefView,
    CreativeReadinessView,
    creative_asset_service,
)
from app.creative_generation import CreativeGenerationView
from app.creative_provider_finalization import provider_aware_creative_generation_service
from app.operator_auth import require_operator
from app.product_intake import product_intake_service

router = APIRouter(
    tags=["creative-assets"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/distribution-actions/{action_id}/creative-brief",
    response_model=CreativeBriefView,
)
async def ensure_creative_brief(action_id: UUID) -> CreativeBriefView:
    try:
        return creative_asset_service.ensure_brief(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionAction dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-actions/{action_id}/creative-readiness",
    response_model=CreativeReadinessView,
)
async def get_creative_readiness(action_id: UUID) -> CreativeReadinessView:
    try:
        return creative_asset_service.readiness(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionAction dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/creative-generate",
    response_model=CreativeGenerationView,
)
async def generate_creative(action_id: UUID) -> CreativeGenerationView:
    try:
        return provider_aware_creative_generation_service.ensure_ready(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="DistributionAction dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/creative-assets",
    response_model=CreativeAssetView,
    status_code=status.HTTP_201_CREATED,
)
async def register_creative_asset(
    payload: CreativeAssetRegisterRequest,
) -> CreativeAssetView:
    try:
        return creative_asset_service.register_asset(payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="CreativeBrief not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/creative-assets/{asset_id}",
    response_model=CreativeAssetView,
)
async def get_creative_asset(asset_id: UUID) -> CreativeAssetView:
    try:
        return creative_asset_service.get_asset(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="CreativeAsset not found") from exc


@router.get(
    "/products/{product_id}/creative-assets",
    response_model=list[CreativeAssetView],
)
async def list_creative_assets(product_id: UUID) -> list[CreativeAssetView]:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return creative_asset_service.list_assets(product_id)


@router.post(
    "/creative-assets/{asset_id}/retire",
    response_model=CreativeAssetView,
)
async def retire_creative_asset(asset_id: UUID) -> CreativeAssetView:
    try:
        return creative_asset_service.retire(asset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="CreativeAsset not found") from exc
