from __future__ import annotations

from uuid import uuid4

import pytest

from app.customer_live_opportunities import CUSTOMER_PROJECT_NAMESPACE
from app.manual_telegram_community_restriction import seed_refresh_verify
from app.runtime_store import MemoryRuntimeStateStore


@pytest.mark.asyncio
async def test_seed_refuses_ephemeral_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryRuntimeStateStore()
    product_id = uuid4()
    project_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {"id": str(project_id), "product_id": str(product_id)},
    )

    monkeypatch.setattr(
        "app.manual_telegram_community_restriction.get_runtime_store",
        lambda: store,
    )

    with pytest.raises(RuntimeError, match="ephemeral runtime storage"):
        await seed_refresh_verify(
            product_id=product_id,
            community_handle="example",
            reason="ACCOUNT_BANNED_IN_COMMUNITY",
            refresh=False,
        )
