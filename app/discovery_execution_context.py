from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID, uuid4


class DiscoveryExecutionMode(StrEnum):
    RUNTIME = "RUNTIME"
    DEBUG_TRACE = "DEBUG_TRACE"
    REPLAY = "REPLAY"


class DiscoveryBudgetExhausted(RuntimeError):
    pass


@dataclass(frozen=True)
class DiscoveryEffectPolicy:
    allow_network: bool
    allow_state_writes: bool
    allow_learning_writes: bool
    allow_external_actions: bool


_POLICIES = {
    DiscoveryExecutionMode.RUNTIME: DiscoveryEffectPolicy(
        allow_network=True,
        allow_state_writes=True,
        allow_learning_writes=True,
        allow_external_actions=True,
    ),
    DiscoveryExecutionMode.DEBUG_TRACE: DiscoveryEffectPolicy(
        allow_network=True,
        allow_state_writes=False,
        allow_learning_writes=False,
        allow_external_actions=False,
    ),
    DiscoveryExecutionMode.REPLAY: DiscoveryEffectPolicy(
        allow_network=False,
        allow_state_writes=False,
        allow_learning_writes=False,
        allow_external_actions=False,
    ),
}


@dataclass
class DiscoveryExecutionContext:
    mode: DiscoveryExecutionMode
    product_id: str | None = None
    run_id: str = field(default_factory=lambda: str(uuid4()))
    max_logical_queries: int | None = None
    _step_sequence: int = field(default=0, init=False, repr=False)
    _query_sequence: int = field(default=0, init=False, repr=False)
    _reserved_logical_queries: int = field(default=0, init=False, repr=False)

    @classmethod
    def create(
        cls,
        mode: DiscoveryExecutionMode,
        product_id: UUID | str | None = None,
        *,
        max_logical_queries: int | None = None,
    ) -> DiscoveryExecutionContext:
        if max_logical_queries is not None and max_logical_queries < 0:
            raise ValueError("max_logical_queries must be non-negative or None")
        return cls(
            mode=mode,
            product_id=(None if product_id is None else str(product_id)),
            max_logical_queries=max_logical_queries,
        )

    @property
    def policy(self) -> DiscoveryEffectPolicy:
        return _POLICIES[self.mode]

    @property
    def logical_query_budget_exhausted(self) -> bool:
        return (
            self.max_logical_queries is not None
            and self._reserved_logical_queries >= self.max_logical_queries
        )

    def next_step_id(self, stage: str) -> str:
        self._step_sequence += 1
        normalized = "_".join(str(stage or "step").strip().casefold().split()) or "step"
        return f"{self.run_id}:step:{self._step_sequence}:{normalized}"

    def reserve_query_id(self) -> str:
        if self.logical_query_budget_exhausted:
            raise DiscoveryBudgetExhausted(
                "Logical discovery query budget exhausted before provider call"
            )
        self._reserved_logical_queries += 1
        self._query_sequence += 1
        return f"{self.run_id}:query:{self._query_sequence}"

    def next_query_id(self) -> str:
        """Backward-compatible unbudgeted ID allocation for non-provider diagnostics."""
        self._query_sequence += 1
        return f"{self.run_id}:query:{self._query_sequence}"

    def as_dict(self) -> dict:
        policy = self.policy
        remaining = (
            None
            if self.max_logical_queries is None
            else max(0, self.max_logical_queries - self._reserved_logical_queries)
        )
        return {
            "run_id": self.run_id,
            "mode": self.mode.value,
            "product_id": self.product_id,
            "effects": {
                "network": policy.allow_network,
                "state_writes": policy.allow_state_writes,
                "learning_writes": policy.allow_learning_writes,
                "external_actions": policy.allow_external_actions,
            },
            "logical_query_budget": {
                "max": self.max_logical_queries,
                "reserved": self._reserved_logical_queries,
                "remaining": remaining,
                "exhausted": self.logical_query_budget_exhausted,
            },
        }
