from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_schemas import (
    CampaignSlotCreateRequest,
    CampaignSlotStatusRequest,
    CommunityPolicyUpsertRequest,
    DistributionIdentityCreateRequest,
    DistributionIdentityStatusRequest,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_schemas import (
    CampaignSlotView,
    CommunityPolicyView,
    DistributionIdentityView,
)
from app.distribution_types import DistributionPlatform
from app.product_intake import product_intake_service

router = APIRouter(tags=["distribution-control-plane"])


@router.post(
    "/distribution-identities",
    response_model=DistributionIdentityView,
    status_code=status.HTTP_201_CREATED,
)
async def create_distribution_identity(
    payload: DistributionIdentityCreateRequest,
) -> DistributionIdentityView:
    return distribution_control_plane_service.create_identity(payload)


@router.get(
    "/distribution-identities",
    response_model=list[DistributionIdentityView],
)
async def list_distribution_identities(
    platform: DistributionPlatform | None = Query(default=None),
) -> list[DistributionIdentityView]:
    return distribution_control_plane_service.list_identities(platform)


@router.patch(
    "/distribution-identities/{identity_id}/status",
    response_model=DistributionIdentityView,
)
async def set_distribution_identity_status(
    identity_id: UUID,
    payload: DistributionIdentityStatusRequest,
) -> DistributionIdentityView:
    try:
        return distribution_control_plane_service.set_identity_status(identity_id, payload.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Distribution Identity not found") from exc


@router.put(
    "/distribution-opportunities/{opportunity_id}/community-policy",
    response_model=CommunityPolicyView,
)
async def upsert_community_policy(
    opportunity_id: UUID,
    payload: CommunityPolicyUpsertRequest,
) -> CommunityPolicyView:
    try:
        audience_intelligence_service.find_opportunity(opportunity_id)
        return distribution_control_plane_service.upsert_policy(opportunity_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Distribution Opportunity not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-opportunities/{opportunity_id}/community-policy",
    response_model=CommunityPolicyView,
)
async def get_community_policy(opportunity_id: UUID) -> CommunityPolicyView:
    try:
        return distribution_control_plane_service.get_policy(opportunity_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="CommunityPolicy not found") from exc


@router.post(
    "/products/{product_id}/campaign-slots",
    response_model=CampaignSlotView,
    status_code=status.HTTP_201_CREATED,
)
async def create_campaign_slot(
    product_id: UUID,
    payload: CampaignSlotCreateRequest,
) -> CampaignSlotView:
    try:
        product_intake_service.get_product(product_id)
        distribution_control_plane_service.get_identity(payload.distribution_identity_id)
        return distribution_control_plane_service.create_campaign_slot(product_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product or Distribution Identity not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/products/{product_id}/campaign-slots",
    response_model=list[CampaignSlotView],
)
async def list_product_campaign_slots(product_id: UUID) -> list[CampaignSlotView]:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc
    return distribution_control_plane_service.list_campaign_slots(product_id)


@router.patch(
    "/campaign-slots/{slot_id}/status",
    response_model=CampaignSlotView,
)
async def set_campaign_slot_status(
    slot_id: UUID,
    payload: CampaignSlotStatusRequest,
) -> CampaignSlotView:
    try:
        return distribution_control_plane_service.set_campaign_slot_status(slot_id, payload.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="CampaignSlot not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
