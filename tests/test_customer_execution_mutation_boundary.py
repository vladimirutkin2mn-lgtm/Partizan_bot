from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.customer_execution_boundary import (
    customer_execution_request_scope,
    require_customer_bound_mutation_scope,
)

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_REQUEST_ID = UUID("22222222-2222-4222-8222-222222222222")


def _action(request_id: UUID | None):
    metadata = {}
    if request_id is not None:
        metadata["customer_execution_request_id"] = str(request_id)
    return SimpleNamespace(operational_metadata=metadata)


def test_customer_bound_mutation_requires_matching_request_scope() -> None:
    action = _action(REQUEST_ID)

    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        require_customer_bound_mutation_scope(action, "execution")

    with customer_execution_request_scope(OTHER_REQUEST_ID):
        with pytest.raises(ValueError, match="dedicated customer execution request flow"):
            require_customer_bound_mutation_scope(action, "execution")

    with customer_execution_request_scope(REQUEST_ID):
        require_customer_bound_mutation_scope(action, "execution")


def test_non_customer_action_does_not_require_scope() -> None:
    require_customer_bound_mutation_scope(_action(None), "execution")


def test_only_request_bound_operator_services_open_customer_scope() -> None:
    app_dir = Path("app")
    openers = []
    for path in app_dir.glob("*.py"):
        if path.name == "customer_execution_boundary.py":
            continue
        source = path.read_text()
        if "customer_execution_request_scope(" in source:
            openers.append(path.name)

    assert sorted(openers) == [
        "customer_operator_approval.py",
        "customer_operator_execution.py",
    ]


def test_all_known_customer_execution_bypasses_check_scope_before_mutation() -> None:
    service_source = Path("app/distribution_execution_service.py").read_text()
    for operation in (
        'require_customer_bound_mutation_scope(action, "approval")',
        'require_customer_bound_mutation_scope(action, "outreach approval")',
        'require_customer_bound_mutation_scope(action, "skip")',
        'require_customer_bound_mutation_scope(action, "completion")',
    ):
        assert operation in service_source

    adapter_source = Path("app/execution_adapters.py").read_text()
    assert 'require_customer_bound_mutation_scope(action, "execution")' in adapter_source

    tiktok_source = Path("app/tiktok_direct_post.py").read_text()
    tiktok_guard = 'require_customer_bound_mutation_scope(action, "TikTok Direct Post")'
    assert tiktok_guard in tiktok_source
    assert tiktok_source.index(tiktok_guard) < tiktok_source.index(
        "existing = self._get_latest_raw(action_id)"
    )

    reddit_source = Path("app/reddit_client_publishing.py").read_text()
    reddit_guard = 'require_customer_bound_mutation_scope(action, "Reddit client publish")'
    assert reddit_guard in reddit_source
    assert reddit_source.index(reddit_guard) < reddit_source.index(
        "return await super().publish"
    )

    telegram_source = Path("app/telegram_client_publishing.py").read_text()
    telegram_guard = 'require_customer_bound_mutation_scope(action, "Telegram client publish")'
    assert telegram_guard in telegram_source
    assert telegram_source.index(telegram_guard) < telegram_source.index(
        "return await super().publish"
    )


def test_publisher_impl_modules_are_private_implementation_only() -> None:
    app_dir = Path("app")
    for path in app_dir.glob("*.py"):
        if path.name in {
            "reddit_client_publishing.py",
            "reddit_client_publishing_impl.py",
            "telegram_client_publishing.py",
            "telegram_client_publishing_impl.py",
        }:
            continue
        source = path.read_text()
        assert "reddit_client_publishing_impl" not in source
        assert "telegram_client_publishing_impl" not in source
