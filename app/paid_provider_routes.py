from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from app.paid_provider_connections import (
    PaidProviderConnectionCreateRequest,
    PaidProviderConnectionView,
    paid_provider_connection_service,
)
from app.product_intake import product_intake_service

router = APIRouter(tags=["paid-provider-connections"])


@router.put(
    "/products/{product_id}/paid-provider-connections/meta",
    response_model=PaidProviderConnectionView,
    status_code=status.HTTP_200_OK,
)
async def upsert_meta_paid_provider_connection(
    product_id: UUID,
    payload: PaidProviderConnectionCreateRequest,
) -> PaidProviderConnectionView:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
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
