from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.customer_account import (
    CUSTOMER_ACCOUNT_SESSION_COOKIE,
    CustomerAccountAuthenticationError,
    customer_account_service,
)
from app.customer_funnel import CustomerProjectAccessError, CustomerProjectNotFoundError
from app.distribution_execution_service import distribution_execution_service
from app.reddit_client_publishing import (
    CustomerRedditClientPublishError,
    RedditClientPublishReceipt,
    RedditConnectionView,
    RedditOAuthStartView,
    RedditPublishObservationView,
    RedditPublishRequest,
    customer_reddit_client_publish_service,
)

router = APIRouter(tags=["customer-reddit"])


class CustomerRedditPublishRequest(BaseModel):
    confirm_publish: bool = False
    retry: bool = False


def _session_cookie(
    session_token: Annotated[str | None, Cookie(alias=CUSTOMER_ACCOUNT_SESSION_COOKIE)] = None,
) -> str | None:
    return session_token


def _project_token(session_token: str | None, project_id: UUID) -> str:
    try:
        _, customer_token = customer_account_service.project_access(
            session_token=session_token,
            project_id=project_id,
        )
        return customer_token
    except CustomerAccountAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except CustomerProjectNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Customer project not found") from exc
    except CustomerProjectAccessError as exc:
        raise HTTPException(status_code=403, detail="This project does not belong to this account") from exc


def _callback_target(*, result: str, project_id: UUID | None) -> str:
    query = {"reddit": result}
    if project_id is not None:
        query["project"] = str(project_id)
    return f"/workspace?{urlencode(query)}"


@router.get(
    "/customer/workspace/{project_id}/reddit/connection",
    response_model=RedditConnectionView,
)
def get_customer_reddit_connection(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> RedditConnectionView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_reddit_client_publish_service.connection(project_id, customer_token)
    except CustomerRedditClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/reddit/connection/start",
    response_model=RedditOAuthStartView,
)
def start_customer_reddit_connection(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> RedditOAuthStartView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_reddit_client_publish_service.begin_connection(project_id, customer_token)
    except CustomerRedditClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/customer/reddit/oauth/callback")
async def complete_customer_reddit_connection(
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    project_id = (
        customer_reddit_client_publish_service.pending_project(state)
        if state
        else None
    )
    if error or not state or not code:
        return RedirectResponse(
            url=_callback_target(result="error", project_id=project_id),
            status_code=303,
        )
    try:
        completed_project_id = await customer_reddit_client_publish_service.complete_connection(
            state=state,
            code=code,
        )
    except CustomerRedditClientPublishError:
        return RedirectResponse(
            url=_callback_target(result="error", project_id=project_id),
            status_code=303,
        )
    return RedirectResponse(
        url=_callback_target(result="connected", project_id=completed_project_id),
        status_code=303,
    )


@router.delete(
    "/customer/workspace/{project_id}/reddit/connection",
    response_model=RedditConnectionView,
)
def disconnect_customer_reddit_connection(
    project_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> RedditConnectionView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_reddit_client_publish_service.disconnect(project_id, customer_token)
    except CustomerRedditClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/reddit/actions/{action_id}/publish",
    response_model=RedditClientPublishReceipt,
)
async def publish_customer_reddit_action(
    project_id: UUID,
    action_id: UUID,
    payload: CustomerRedditPublishRequest,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> RedditClientPublishReceipt:
    customer_token = _project_token(session_token, project_id)
    if not payload.confirm_publish:
        raise HTTPException(
            status_code=409,
            detail="Explicit customer confirmation is required for each Reddit publish",
        )
    try:
        return await customer_reddit_client_publish_service.publish(
            project_id,
            customer_token,
            action_id,
            RedditPublishRequest(
                confirm_publish=payload.confirm_publish,
                retry=payload.retry,
            ),
        )
    except CustomerRedditClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/customer/workspace/{project_id}/reddit/actions/{action_id}/observe",
    response_model=RedditPublishObservationView,
)
async def observe_customer_reddit_action(
    project_id: UUID,
    action_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> RedditPublishObservationView:
    customer_token = _project_token(session_token, project_id)
    try:
        view = await customer_reddit_client_publish_service.observe_publish(
            project_id,
            customer_token,
            action_id,
        )
        if view.checked_at is not None and view.state is not None:
            distribution_execution_service.record_external_observation(
                action_id,
                provider="reddit",
                observation={
                    "state": view.state.value,
                    "score": view.score,
                    "reply_count": view.reply_count,
                    "restriction_signal": view.restriction_signal,
                    "checked_at": view.checked_at.isoformat(),
                    "executed_url": str(view.executed_url) if view.executed_url else None,
                },
            )
        return view
    except CustomerRedditClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/customer/workspace/{project_id}/reddit/actions/{action_id}/observation",
    response_model=RedditPublishObservationView,
)
def get_customer_reddit_action_observation(
    project_id: UUID,
    action_id: UUID,
    session_token: Annotated[str | None, Depends(_session_cookie)] = None,
) -> RedditPublishObservationView:
    customer_token = _project_token(session_token, project_id)
    try:
        return customer_reddit_client_publish_service.get_observation(
            project_id,
            customer_token,
            action_id,
        )
    except CustomerRedditClientPublishError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
