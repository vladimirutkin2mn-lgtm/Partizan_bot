from dataclasses import dataclass
from uuid import UUID


@dataclass(slots=True)
class WorkflowStage:
    name: str
    status: str


def build_mock_growth_workflow(product_id: UUID) -> list[WorkflowStage]:
    return [
        WorkflowStage(name="product_profile", status="ready"),
        WorkflowStage(name="icp_generation", status="pending"),
        WorkflowStage(name="channel_discovery", status="pending"),
        WorkflowStage(name="growth_play_generation", status="pending"),
        WorkflowStage(name="experiment", status="pending"),
        WorkflowStage(name="measurement", status="pending"),
        WorkflowStage(name="decision", status="pending"),
    ]
