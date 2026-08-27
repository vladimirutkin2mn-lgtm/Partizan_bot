from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.growth_autoresearch import growth_autoresearch_service
from app.growth_autoresearch_schemas import (
    GrowthChampionView,
    GrowthResearchBaselineRequest,
    GrowthResearchChallengerRequest,
    GrowthResearchEvaluationRequest,
    GrowthResearchEvaluationView,
    GrowthResearchHistoryView,
    GrowthResearchPolicyRequest,
    GrowthResearchPolicyView,
    GrowthResearchTrialView,
)
from app.product_intake import product_intake_service

router = APIRouter(tags=["growth-autoresearch"])


def _require_product(product_id: UUID) -> None:
    try:
        product_intake_service.get_product(product_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Product not found") from exc


@router.put(
    "/products/{product_id}/growth-autoresearch/policy",
    response_model=GrowthResearchPolicyView,
)
def configure_growth_autoresearch_policy(
    product_id: UUID,
    payload: GrowthResearchPolicyRequest,
) -> GrowthResearchPolicyView:
    _require_product(product_id)
    return growth_autoresearch_service.configure_policy(product_id, payload)


@router.post(
    "/products/{product_id}/growth-autoresearch/baseline",
    response_model=GrowthChampionView,
)
def establish_growth_autoresearch_baseline(
    product_id: UUID,
    payload: GrowthResearchBaselineRequest,
) -> GrowthChampionView:
    _require_product(product_id)
    try:
        return growth_autoresearch_service.establish_baseline(product_id, payload)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Configure Growth AutoResearch policy before establishing a baseline",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/products/{product_id}/growth-autoresearch/trials",
    response_model=GrowthResearchTrialView,
)
def create_growth_autoresearch_trial(
    product_id: UUID,
    payload: GrowthResearchChallengerRequest,
) -> GrowthResearchTrialView:
    _require_product(product_id)
    try:
        return growth_autoresearch_service.create_challenger(product_id, payload)
    except KeyError as exc:
        raise HTTPException(
            status_code=409,
            detail="Configure Growth AutoResearch policy before creating a challenger",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/growth-autoresearch/trials/{trial_id}/evaluate",
    response_model=GrowthResearchEvaluationView,
)
def evaluate_growth_autoresearch_trial(
    trial_id: UUID,
    payload: GrowthResearchEvaluationRequest,
) -> GrowthResearchEvaluationView:
    try:
        return growth_autoresearch_service.evaluate_trial(trial_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Growth AutoResearch trial not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/products/{product_id}/growth-autoresearch",
    response_model=GrowthResearchHistoryView,
)
def get_growth_autoresearch_history(product_id: UUID) -> GrowthResearchHistoryView:
    _require_product(product_id)
    return growth_autoresearch_service.history(product_id)
