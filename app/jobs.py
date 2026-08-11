from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

JobHandler = Callable[..., Awaitable[Any]]


class JobQueue(Protocol):
    async def enqueue(self, handler: JobHandler, *args: Any, **kwargs: Any) -> str:
        ...


@dataclass(slots=True)
class InlineJobQueue:
    completed_jobs: dict[str, Any] = field(default_factory=dict)
    _sequence: int = 0

    async def enqueue(self, handler: JobHandler, *args: Any, **kwargs: Any) -> str:
        self._sequence += 1
        job_id = f"inline-{self._sequence}"
        self.completed_jobs[job_id] = await handler(*args, **kwargs)
        return job_id


async def mock_growth_job(product_id: str) -> dict[str, str]:
    return {
        "product_id": product_id,
        "status": "mock_completed",
        "next_stage": "icp_generation",
    }
