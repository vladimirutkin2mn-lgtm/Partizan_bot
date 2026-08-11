from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.paid_provider_connections import (
    PaidProviderConnectionCreateRequest,
    PaidProviderConnectionView,
    paid_provider_connection_service,
)
from app.product_intake import product_intake_service
from app.tiktok_paid_provider import (
    TikTokPaidProviderConnectionCreateRequest,
    TikTokPaidProviderConnectionView,
    tiktok_paid_provider_connection_service,
)

router = APIRouter(tags=["paid-provider-connections"])


def _require_product(product_id: UUID) -> None:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc


@router.put(
    "/products/{product_id}/paid-provider-connections/meta",
    response_model=PaidProviderConnectionView,
    status_code=status.HTTP_200_OK,
)
async def upsert_meta_paid_provider_connection(
    product_id: UUID,
    payload: PaidProviderConnectionCreateRequest,
) -> PaidProviderConnectionView:
    _require_product(product_id)
    return paid_provider_connection_service.upsert_meta(product_id, payload)


@router.get(
    "/products/{product_id}/paid-provider-connections/meta",
    response_model=PaidProviderConnectionView,
)
async def get_meta_paid_provider_connection(
    product_id: UUID,
) -> PaidProviderConnectionView:
    connection = paid_provider_connection_service.get_meta(product_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="Meta paid provider connection not found")
    return connection


@router.put(
    "/products/{product_id}/paid-provider-connections/tiktok",
    response_model=TikTokPaidProviderConnectionView,
    status_code=status.HTTP_200_OK,
)
async def upsert_tiktok_paid_provider_connection(
    product_id: UUID,
    payload: TikTokPaidProviderConnectionCreateRequest,
) -> TikTokPaidProviderConnectionView:
    _require_product(product_id)
    return tiktok_paid_provider_connection_service.upsert(product_id, payload)


@router.get(
    "/products/{product_id}/paid-provider-connections/tiktok",
    response_model=TikTokPaidProviderConnectionView,
)
async def get_tiktok_paid_provider_connection(
    product_id: UUID,
) -> TikTokPaidProviderConnectionView:
    connection = tiktok_paid_provider_connection_service.get(product_id)
    if connection is None:
        raise HTTPException(status_code=404, detail="TikTok paid provider connection not found")
    return connection
