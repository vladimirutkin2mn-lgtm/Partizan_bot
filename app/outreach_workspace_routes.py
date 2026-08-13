from uuid import UUID

from fastapi import APIRouter, Depends

from app.operator_auth import require_operator
from app.outreach_workspace import OutreachWorkspaceView, outreach_workspace_service

router = APIRouter(
    tags=["outreach"],
    dependencies=[Depends(require_operator)],
)


@router.get(
    "/products/{product_id}/outreach-workspace",
    response_model=OutreachWorkspaceView,
)
async def get_outreach_workspace(product_id: UUID) -> OutreachWorkspaceView:
    return outreach_workspace_service.get(product_id)
