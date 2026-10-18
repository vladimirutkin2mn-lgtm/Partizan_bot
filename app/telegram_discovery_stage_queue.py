"""Persistent per-run Telegram discovery stage ledger.

A staged, restart-safe diagnostic queue layered on top of existing native,
catalog and paid guards. It NEVER schedules extra network calls or authorizes
publishing. Each run owns unique checkpoint keys, so concurrent runs cannot
clobber one another's stage outcomes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.runtime_store import RuntimeStateStore

TELEGRAM_DISCOVERY_STAGE_NAMESPACE = "telegram_discovery_stage"
TELEGRAM_DISCOVERY_LATEST_RUN_NAMESPACE = "telegram_discovery_latest_run"
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

    def mark(
        self,
        stage: str,
        status: str,
        *,
        reason: str,
        candidate_count: int = 0,
        next_attempt_at: str | None = None,
        error_type: str | None = None,
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
