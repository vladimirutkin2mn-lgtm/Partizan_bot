import logging
import sys

import pytest

from app import autonomous_growth_worker, paid_control_worker
from app.logging import JsonFormatter


class _StubWorker:
    def run(self, **_: object) -> int:
        return 0


@pytest.fixture
def bare_root_logger():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    root.handlers = []
    root.setLevel(logging.WARNING)
    yield root
    root.handlers = handlers
    root.setLevel(level)


@pytest.mark.parametrize(
    ("module", "worker_class"),
    [
        (autonomous_growth_worker, "AutonomousGrowthWorker"),
        (paid_control_worker, "PaidControlWorker"),
    ],
)
def test_worker_main_configures_json_logging(monkeypatch, bare_root_logger, module, worker_class) -> None:
    monkeypatch.setattr(module, worker_class, _StubWorker)
    monkeypatch.setattr(sys, "argv", ["worker", "--once"])

    assert module.main() == 0

    assert bare_root_logger.level == logging.INFO
    assert [type(handler.formatter) for handler in bare_root_logger.handlers] == [JsonFormatter]
