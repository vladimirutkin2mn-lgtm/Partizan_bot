from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.managed_distribution_routes as routes


def test_assignment_without_profile_strategy_does_not_add_profile_gate(monkeypatch) -> None:
    assignment_id = uuid4()
    monkeypatch.setattr(
        routes.managed_distribution_service,
        "get_assignment",
        lambda value: SimpleNamespace(
            id=value,
            profile_strategy_key=None,
        ),
    )

    routes._require_exact_managed_telegram_profile_strategy(assignment_id)


def test_profile_strategy_assignment_is_blocked_until_exact_treatment_is_applied(
    monkeypatch,
) -> None:
    assignment_id = uuid4()
    monkeypatch.setattr(
        routes.managed_distribution_service,
        "get_assignment",
        lambda value: SimpleNamespace(
            id=value,
            profile_strategy_key="expert-native-cta-v1",
        ),
    )
    monkeypatch.setattr(
        routes.managed_telegram_profile_strategy_service,
        "is_exact_strategy_applied",
        lambda value: False,
    )

    with pytest.raises(HTTPException) as exc:
        routes._require_exact_managed_telegram_profile_strategy(assignment_id)

    assert exc.value.status_code == 409
    assert "must be APPLIED" in str(exc.value.detail)


def test_profile_strategy_assignment_passes_only_after_exact_treatment_is_applied(
    monkeypatch,
) -> None:
    assignment_id = uuid4()
    monkeypatch.setattr(
        routes.managed_distribution_service,
        "get_assignment",
        lambda value: SimpleNamespace(
            id=value,
            profile_strategy_key="expert-native-cta-v1",
        ),
    )
    calls = []

    def applied(value):
        calls.append(value)
        return True

    monkeypatch.setattr(
        routes.managed_telegram_profile_strategy_service,
        "is_exact_strategy_applied",
        applied,
    )

    routes._require_exact_managed_telegram_profile_strategy(assignment_id)

    assert calls == [assignment_id]
