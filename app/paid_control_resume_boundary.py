from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

_paid_control_resume_action: ContextVar[str | None] = ContextVar(
    "paid_control_resume_action",
    default=None,
)


def require_paid_control_resume_scope(action_id: UUID) -> None:
    if _paid_control_resume_action.get() != str(action_id):
        raise ValueError(
            "Paid provider resume requires an explicit customer Autopilot resume scope"
        )


@contextmanager
def paid_control_resume_scope(action_id: UUID):
    token = _paid_control_resume_action.set(str(action_id))
    try:
        yield
    finally:
        _paid_control_resume_action.reset(token)
