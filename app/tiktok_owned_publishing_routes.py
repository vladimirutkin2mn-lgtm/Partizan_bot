from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.operator_auth import require_operator
from app.tiktok_direct_post import (
    TikTokDirectPostAttemptView,
    tiktok_direct_post_service,
)
from app.tiktok_direct_post_reconciliation import (
    TikTokDirectPostReconciliationView,
    TikTokPostStatusApiError,
    tiktok_direct_post_reconciliation_service,
)
from app.tiktok_owned_publishing import (
    TikTokCreatorInfoApiError,
    TikTokCreatorPublishPreflightView,
    tiktok_creator_publish_preflight_service,
)
from app.tiktok_publish_authorization import (
    TikTokPublishAuthorizationCreateRequest,
    TikTokPublishAuthorizationView,
    tiktok_publish_authorization_service,
)

router = APIRouter(
    tags=["owned-publishing"],
    dependencies=[Depends(require_operator)],
)


@router.post(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/preflight",
    response_model=TikTokCreatorPublishPreflightView,
)
async def refresh_tiktok_creator_preflight(
    action_id: UUID,
) -> TikTokCreatorPublishPreflightView:
    try:
        return tiktok_creator_publish_preflight_service.refresh(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Publishing dependency not found") from exc
    except TikTokCreatorInfoApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/preflight",
    response_model=TikTokCreatorPublishPreflightView,
)
async def get_tiktok_creator_preflight(
    action_id: UUID,
) -> TikTokCreatorPublishPreflightView:
    try:
        return tiktok_creator_publish_preflight_service.get_latest(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok creator preflight not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/authorization",
    response_model=TikTokPublishAuthorizationView,
    status_code=status.HTTP_201_CREATED,
)
async def authorize_tiktok_publish(
    action_id: UUID,
    payload: TikTokPublishAuthorizationCreateRequest,
) -> TikTokPublishAuthorizationView:
    try:
        return tiktok_publish_authorization_service.authorize(action_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Publishing dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/authorization",
    response_model=TikTokPublishAuthorizationView,
)
async def get_tiktok_publish_authorization(
    action_id: UUID,
) -> TikTokPublishAuthorizationView:
    try:
        return tiktok_publish_authorization_service.get_current(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok publish authorization not found") from exc


@router.post(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/authorization/revoke",
    response_model=TikTokPublishAuthorizationView,
)
async def revoke_tiktok_publish_authorization(
    action_id: UUID,
) -> TikTokPublishAuthorizationView:
    try:
        return tiktok_publish_authorization_service.revoke(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok publish authorization not found") from exc


@router.post(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/direct-post",
    response_model=TikTokDirectPostAttemptView,
)
async def submit_tiktok_direct_post(action_id: UUID) -> TikTokDirectPostAttemptView:
    try:
        return tiktok_direct_post_service.submit(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Publishing dependency not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/direct-post",
    response_model=TikTokDirectPostAttemptView,
)
async def get_tiktok_direct_post(action_id: UUID) -> TikTokDirectPostAttemptView:
    try:
        return tiktok_direct_post_service.get_latest(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="TikTok Direct Post attempt not found") from exc


@router.post(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/direct-post/reconcile",
    response_model=TikTokDirectPostReconciliationView,
)
async def reconcile_tiktok_direct_post(
    action_id: UUID,
) -> TikTokDirectPostReconciliationView:
    try:
        return tiktok_direct_post_reconciliation_service.reconcile(action_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Publishing dependency not found") from exc
    except TikTokPostStatusApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/distribution-actions/{action_id}/owned-publishing/tiktok/direct-post/reconciliation",
    response_model=TikTokDirectPostReconciliationView,
)
async def get_tiktok_direct_post_reconciliation(
    action_id: UUID,
) -> TikTokDirectPostReconciliationView:
    try:
        return tiktok_direct_post_reconciliation_service.get_latest(action_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="TikTok Direct Post reconciliation not found",
        ) from exc
