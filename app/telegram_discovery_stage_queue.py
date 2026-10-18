"""Persistent per-run Telegram discovery stage ledger.

A staged, restart-safe diagnostic queue layered on top of existing native,
catalog and paid guards. It NEVER schedules extra network calls or authorizes
publishing. Each run owns unique checkpoint keys, so concurrent runs cannot
clobber one another's stage outcomes.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.runtime_store import RuntimeStateStore, get_runtime_store

TELEGRAM_DISCOVERY_STAGE_NAMESPACE = "telegram_discovery_stage"
TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE = "telegram_discovery_latest_run"
TELEGRAM_DISCOVERY_RETENTION_NAMESPACE = "telegram_discovery_retention"
TELEGRAM_DISCOVERY_MAX_HISTORY_RUNS = 64
TELEGRAM_DISCOVERY_MAX_HISTORY_DAYS = 30
TELEGRAM_DISCOVERY_STAGES = ("registry", "native", "catalog", "paid")
TERMINAL_STATUSES = {
    "COMPLETED", "NO_FIT", "STAGED_UNVERIFIED", "FAILED_RETRYABLE",
    "SKIPPED_DISABLED", "SKIPPED_SUFFICIENT", "SKIPPED_NO_CANDIDATES",
    "SKIPPED_OTHER_SOURCE", "DEFERRED_COOLDOWN", "DEFERRED_RATE_LIMIT",
    "BLOCKED", "BUDGET_EXHAUSTED", "NOT_APPLICABLE",
}


class TelegramDiscoveryStageQueue:
    """One deterministic four-stage checkpoint set per product/discovery run."""

    def __init__(
        self,
        store: RuntimeStateStore,
        product_id: object,
        *,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> None:
        self._store = store
        self.product_id = str(product_id)
        self.run_id = run_id or str(uuid4())
        at = now or datetime.now(UTC)
        self.started_at = at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)
        self._started = False

    def _key(self, stage: str) -> str:
        if stage not in TELEGRAM_DISCOVERY_STAGES:
            raise ValueError("Unsupported Telegram discovery stage")
        return f"{self.product_id}:{self.run_id}:{stage}"

    def start(self) -> None:
        if self._started:
            return
        for priority, stage in enumerate(TELEGRAM_DISCOVERY_STAGES):
            self._store.put_if_absent(
                TELEGRAM_DISCOVERY_STAGE_NAMESPACE,
                self._key(stage),
                {
                    "id": self._key(stage),
                    "product_id": self.product_id,
                    "run_id": self.run_id,
                    "stage": stage,
                    "priority": priority,
                    "status": "PENDING",
                    "reason": "NOT_STARTED",
                    "candidate_count": 0,
                    "source": None,
                    "query": None,
                    "gate": None,
                    "started_at": self.started_at.isoformat(),
                    "finished_at": None,
                    "next_attempt_at": None,
                },
            )
        # The latest-run pointer is for convenience only. All individual
        # run checkpoints retain their identity under concurrent workers.
        self._store.put(
            TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE,
            self.product_id,
            {
                "product_id": self.product_id,
                "run_id": self.run_id,
                "started_at": self.started_at.isoformat(),
            },
        )
        self._started = True
        # Once-daily bounded housekeeping, never a new background worker.
        # A cleanup failure cannot prevent discovery or permit publication.
        try:
            self._prune_if_due()
        except Exception:
            pass

    @staticmethod
    def _utc(value: object) -> datetime | None:
        try:
            timestamp = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return (
            timestamp.replace(tzinfo=UTC)
            if timestamp.tzinfo is None
            else timestamp.astimezone(UTC)
        )

    def _prune_if_due(self) -> None:
        state = self._store.get(TELEGRAM_DISCOVERY_RETENTION_NAMESPACE, self.product_id) or {}
        last = self._utc(state.get("checked_at"))
        if last is not None and last.date() >= self.started_at.date():
            return
        removed = self.prune(now=self.started_at)
        self._store.put(
            TELEGRAM_DISCOVERY_RETENTION_NAMESPACE,
            self.product_id,
            {
                "product_id": self.product_id,
                "checked_at": self.started_at.isoformat(),
                "removed_stage_rows": removed,
                "retention_days": TELEGRAM_DISCOVERY_MAX_HISTORY_DAYS,
                "max_runs": TELEGRAM_DISCOVERY_MAX_HISTORY_RUNS,
            },
        )

    def prune(self, *, now: datetime | None = None) -> int:
        """Prune only old *checkpoint rows*, never the source budgets or registry.

        Keep the latest run even if an old worker is still finishing. The
        previous run data is advisory diagnostics; deleting it does not affect
        any provider or publishing idempotency guarantees.
        """
        checked = now or datetime.now(UTC)
        checked = checked.replace(tzinfo=UTC) if checked.tzinfo is None else checked.astimezone(UTC)
        cutoff = checked - timedelta(days=TELEGRAM_DISCOVERY_MAX_HISTORY_DAYS)
        pointer = self._store.get(TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE, self.product_id) or {}
        latest_run_id = str(pointer.get("run_id") or "")
        groups: dict[str, list[dict]] = {}
        for row in self._store.list_namespace(TELEGRAM_DISCOVERY_STAGE_NAMESPACE):
            if str(row.get("product_id")) != self.product_id:
                continue
            run_id = str(row.get("run_id") or "")
            if run_id and row.get("stage") in TELEGRAM_DISCOVERY_STAGES:
                groups.setdefault(run_id, []).append(row)

        ranked = sorted(
            groups,
            key=lambda run_id: max(
                (
                    self._utc(row.get("started_at"))
                    or datetime.min.replace(tzinfo=UTC)
                    for row in groups[run_id]
                ),
                default=datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        removed = 0
        for index, run_id in enumerate(ranked):
            started = max(
                (
                    self._utc(row.get("started_at"))
                    or datetime.min.replace(tzinfo=UTC)
                    for row in groups[run_id]
                ),
                default=datetime.min.replace(tzinfo=UTC),
            )
            if run_id == latest_run_id or (index < TELEGRAM_DISCOVERY_MAX_HISTORY_RUNS and started >= cutoff):
                continue
            for row in groups[run_id]:
                expected_id = f"{self.product_id}:{run_id}:{row['stage']}"
                if str(row.get("id") or "") != expected_id:
                    continue
                self._store.delete(TELEGRAM_DISCOVERY_STAGE_NAMESPACE, expected_id)
                removed += 1
        return removed

    def mark(
        self,
        stage: str,
        status: str,
        *,
        reason: str,
        candidate_count: int = 0,
        next_attempt_at: str | None = None,
        error_type: str | None = None,
        source: str | None = None,
        query: str | None = None,
        gate: str | None = None,
    ) -> dict:
        if status not in TERMINAL_STATUSES:
            raise ValueError("Unsupported Telegram discovery stage status")
        self.start()
        row = self._store.get(TELEGRAM_DISCOVERY_STAGE_NAMESPACE, self._key(stage))
        if row is None:
            raise RuntimeError("Missing Telegram discovery stage checkpoint")
        row.update(
            {
                "status": status,
                "reason": str(reason or "")[:100],
                "candidate_count": max(0, int(candidate_count)),
                "finished_at": datetime.now(UTC).isoformat(),
                "next_attempt_at": next_attempt_at,
                "error_type": error_type,
                "source": str(source or "")[:60] or None,
                "query": str(query or "")[:220] or None,
                "gate": str(gate or "")[:100] or None,
            }
        )
        self._store.put(TELEGRAM_DISCOVERY_STAGE_NAMESPACE, self._key(stage), row)
        return row

    def snapshot(self) -> dict:
        rows = []
        for stage in TELEGRAM_DISCOVERY_STAGES:
            row = self._store.get(TELEGRAM_DISCOVERY_STAGE_NAMESPACE, self._key(stage))
            if row is not None:
                rows.append(row)
        return {
            "product_id": self.product_id,
            "run_id": self.run_id,
            "started_at": self.started_at.isoformat(),
            "stages": rows,
            "complete": len(rows) == len(TELEGRAM_DISCOVERY_STAGES)
            and all(row["status"] in TERMINAL_STATUSES for row in rows),
        }

    @classmethod
    def latest(cls, store: RuntimeStateStore, product_id: object) -> dict | None:
        pointer = store.get(TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE, str(product_id))
        if not pointer:
            return None
        queue = cls(
            store,
            product_id,
            run_id=str(pointer["run_id"]),
            now=datetime.fromisoformat(pointer["started_at"]),
        )
        return queue.snapshot()


def main() -> None:
    """Read-only diagnostic CLI; no new discovery or Telegram account calls."""
    parser = argparse.ArgumentParser(description="Show latest Telegram discovery stage queue")
    parser.add_argument("--product-id", required=True)
    args = parser.parse_args()
    snapshot = TelegramDiscoveryStageQueue.latest(
        get_runtime_store(), args.product_id
    )
    print(json.dumps(snapshot or {"status": "NO_RECORDED_RUN"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
