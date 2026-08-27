from __future__ import annotations

from app.autonomous_growth_worker import AutonomousGrowthWorker


class _Result:
    def model_dump_json(self) -> str:
        return '{"source":"autonomous-growth"}'


class _AutoResearchExecution:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def run_once(self, *, product_id=None):
        self._events.append("autoresearch-execution")
        return object()


class _AutonomousSweep:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def run_once(self, *, product_id=None):
        assert self._events == ["autoresearch-execution"]
        self._events.append("autonomous-growth")
        return _Result()


class _ResearchLoop:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    async def run_once(self, *, product_id=None):
        assert self._events == ["autoresearch-execution", "autonomous-growth"]
        self._events.append("research-loop")
        return object()


def test_worker_gives_live_autoresearch_trial_first_access_to_execution() -> None:
    events: list[str] = []
    emitted: list[str] = []
    worker = AutonomousGrowthWorker(
        sweep_service=_AutonomousSweep(events),
        autoresearch_execution_service=_AutoResearchExecution(events),
        autoresearch_loop_service=_ResearchLoop(events),
    )

    result = worker.run(
        once=True,
        interval_seconds=300,
        emit=emitted.append,
    )

    assert result == 0
    assert events == ["autoresearch-execution", "autonomous-growth", "research-loop"]
    assert emitted == ['{"source":"autonomous-growth"}']
