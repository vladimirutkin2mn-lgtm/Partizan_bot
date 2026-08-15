from fastapi import APIRouter, Depends

from app.operator_auth import require_operator
from app.worker_health import WorkerHealthView, worker_heartbeat_service

router = APIRouter(prefix="/ops/workers", tags=["worker-health"])


@router.get(
    "/health",
    response_model=WorkerHealthView,
    dependencies=[Depends(require_operator)],
)
async def get_worker_health() -> WorkerHealthView:
    return worker_heartbeat_service.health()
