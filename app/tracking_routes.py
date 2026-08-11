from __future__ import annotations

import logging
import re
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.distribution_analytics_schemas import DistributionAnalyticsEventCreate
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionExperimentStatus, DistributionExperimentView
from app.distribution_execution_service import (
    DistributionTrackingLinkBuilder,
    distribution_execution_service,
)
from app.distribution_play_service import distribution_play_service

TRACKING_VISITOR_COOKIE = "ptz_vid"
_VISITOR_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_logger = logging.getLogger(__name__)

router = APIRouter(tags=["tracking"])
_tracking_builder = DistributionTrackingLinkBuilder()


@router.get("/r/{referral_token}", include_in_schema=False)
async def distribution_tracking_redirect(
    referral_token: str,
    request: Request,
    visitor_cookie: Annotated[str | None, Cookie(alias=TRACKING_VISITOR_COOKIE)] = None,
) -> RedirectResponse:
    try:
        experiment, _ = distribution_execution_service.resolve_experiment(
            referral_token=referral_token
        )
        destination = _tracking_destination(experiment)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Tracking link not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Tracking destination unavailable") from exc

    visitor_id, set_cookie = _visitor_id(visitor_cookie)
    if experiment.status in {
        DistributionExperimentStatus.RUNNING,
        DistributionExperimentStatus.FINISHED,
    }:
        _record_visit_best_effort(experiment, visitor_id)

    response = RedirectResponse(url=destination, status_code=302)
    if set_cookie:
        response.set_cookie(
            TRACKING_VISITOR_COOKIE,
            visitor_id,
            max_age=31_536_000,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
    return response


def _tracking_destination(experiment: DistributionExperimentView) -> str:
    action = distribution_execution_service.get_action(experiment.action_id)
    play = distribution_play_service.find(
        experiment.product_id,
        experiment.distribution_play_id,
    )
    destination_url = str(action.operational_metadata.get("destination_url") or "")
    tracking_base = destination_url

    if (
        action.campaign_slot_id is not None
        and play.attribution_level.value in {"PROFILE", "CAMPAIGN"}
    ):
        slot = next(
            (
                item
                for item in distribution_control_plane_service.list_campaign_slots(
                    experiment.product_id
                )
                if item.id == action.campaign_slot_id
            ),
            None,
        )
        if slot is not None and slot.attribution_route:
            tracking_base = slot.attribution_route

    destination = _tracking_builder.build_destination(
        tracking_base,
        product_id=experiment.product_id,
        play_id=play.id,
        opportunity_id=experiment.opportunity_id,
        action_id=action.id,
        experiment_id=experiment.id,
        medium=play.tactic_class.value,
    )
    parts = urlsplit(destination)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("Tracking destination must be absolute http(s)")
    return destination


def _visitor_id(value: str | None) -> tuple[str, bool]:
    if value and _VISITOR_ID_PATTERN.fullmatch(value):
        return value, False
    return uuid4().hex, True


def _record_visit_best_effort(
    experiment: DistributionExperimentView,
    visitor_id: str,
) -> None:
    try:
        distribution_analytics_service.ingest_event(
            DistributionAnalyticsEventCreate(
                event_id=uuid4(),
                event_type="VISIT",
                experiment_id=experiment.id,
                actor_id=f"visitor:{visitor_id}",
                properties={"source": "PARTIZAN_REDIRECT"},
            )
        )
    except Exception:
        _logger.warning(
            "tracking_visit_ingest_failed",
            extra={"experiment_id": str(experiment.id)},
            exc_info=True,
        )
