"""Guard against clearing new bans or treating read-only evidence as send approval."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.femdom_asfera_stale_restriction_reconcile import (
    _EXPECTED_RESTRICTION,
    reconcile,
)
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


class Memory:
    def __init__(self, row):
        self.row = row
        self.clears = 0

    def get(self, *_):
        return self.row.copy() if self.row is not None else None

    def clear(self, *_):
        self.clears += 1
        self.row = None


class Checker:
    def __init__(self, status="READY"):
        self.status = status
        self.calls = 0

    async def check_comment_target(self, *_):
        self.calls += 1
        return TelegramOpportunityPreflightResult(
            status=self.status, handle="a_sfera", post_id=2117,
            linked_chat_id=1582097097,
        )


def evidence(*, fresh="READY", discussion="DISCUSSION_ROOT_VERIFIED"):
    return {
        "status": "ASFERA_READONLY_RECHECK_COMPLETE",
        "fresh_native_preflight": {
            "status": fresh, "handle": "a_sfera", "post_id": 2117,
            "linked_chat_id": 1582097097,
        },
        "native_discussion": {
            "status": discussion, "expected_post_id": 2117,
            "actual_post_id": 2117,
        },
        "fresh_route_verified": (
            fresh == "READY" and discussion == "DISCUSSION_ROOT_VERIFIED"
        ),
    }


@pytest.mark.asyncio
async def test_recover_only_exact_old_record_after_fresh_native_and_root():
    memory = Memory(_EXPECTED_RESTRICTION.copy())
    checker = Checker()

    async def inspect(**_):
        return evidence()

    result = await reconcile(
        memory=memory, audit_runner=inspect, post_checker=checker,
    )
    assert result["status"] == "STALE_RESTRICTION_CLEARED_CURRENT_NATIVE_READY"
    assert memory.clears == 1
    assert checker.calls == 1
    assert result["post_clear_preflight"]["status"] == "READY"
    assert result["send_allowed"] is False
    assert result["publication_authorized"] is False
    assert result["telegram_sends"] == 0
    assert result["telegram_joins"] == 0


@pytest.mark.asyncio
async def test_do_not_clear_a_newer_ban_or_different_source():
    updated = {**_EXPECTED_RESTRICTION,
               "last_observed_at": "2026-10-25T18:00:00+00:00"}
    memory = Memory(updated)

    async def should_not_check(**_):
        raise AssertionError("Stale evidence must never revalidate new ban")

    result = await reconcile(memory=memory, audit_runner=should_not_check)
    assert result["status"] == "BLOCKED_RESTRICTION_CHANGED"
    assert memory.clears == 0


@pytest.mark.asyncio
async def test_composer_alone_or_native_join_required_not_sufficient():
    memory = Memory(_EXPECTED_RESTRICTION.copy())

    async def inspect(**_):
        return evidence(fresh="JOIN_REQUIRED", discussion="NOT_CHECKED")

    result = await reconcile(memory=memory, audit_runner=inspect)
    assert result["status"] == "BLOCKED_FRESH_NATIVE_EVIDENCE_INSUFFICIENT"
    assert memory.clears == 0


@pytest.mark.asyncio
async def test_missing_real_discussion_route_blocks_cleanup():
    memory = Memory(_EXPECTED_RESTRICTION.copy())

    async def inspect(**_):
        return evidence(discussion="TARGET_NO_LONGER_LATEST")

    result = await reconcile(memory=memory, audit_runner=inspect)
    assert result["status"] == "BLOCKED_FRESH_NATIVE_EVIDENCE_INSUFFICIENT"
    assert memory.clears == 0


@pytest.mark.asyncio
async def test_concurrent_restriction_change_blocks_clear():
    memory = Memory(_EXPECTED_RESTRICTION.copy())

    async def inspect(**_):
        memory.row["last_observed_at"] = "2026-10-25T18:00:00+00:00"
        return evidence()

    result = await reconcile(memory=memory, audit_runner=inspect)
    assert result["status"] == "BLOCKED_RESTRICTION_CHANGED_DURING_CHECK"
    assert memory.clears == 0


@pytest.mark.asyncio
async def test_repeated_run_idempotently_reads_but_does_not_reclear():
    memory = Memory(None)

    async def inspect(**_):
        return evidence()

    result = await reconcile(memory=memory, audit_runner=inspect)
    assert result["status"] == "ALREADY_CLEARED_CURRENT_NATIVE_ROUTE_READY"
    assert memory.clears == 0
    assert result["telegram_sends"] == 0


@pytest.mark.asyncio
async def test_invalid_project_rejected_before_store():
    memory = Memory(_EXPECTED_RESTRICTION.copy())
    with pytest.raises(ValueError, match="Outside pinned"):
        await reconcile(memory=memory, project_id=uuid4())
    assert memory.clears == 0
