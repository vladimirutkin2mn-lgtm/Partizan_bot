from types import SimpleNamespace
from uuid import uuid4

from app.community_first_telegram_adapter import TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE
from app.discovery_execution_context import DiscoveryExecutionContext, DiscoveryExecutionMode
from app.distribution_types import OpportunityKind
from app.manual_discovery_trace import _TraceCommunityFirstTelegramDiscoveryAdapter
from app.runtime_store import MemoryRuntimeStateStore


def _candidate() -> SimpleNamespace:
    return SimpleNamespace(
        canonical_key="telegram:123456:community",
        title="Test community",
        url="https://t.me/test_community",
        kind=OpportunityKind.CHANNEL,
        metadata={
            "telegram_entity_id": 123456,
            "handle": "test_community",
            "community_fit_pass": True,
        },
    )


def test_debug_trace_policy_allows_read_network_but_blocks_mutations() -> None:
    context = DiscoveryExecutionContext.create(
        DiscoveryExecutionMode.DEBUG_TRACE,
        uuid4(),
    )

    assert context.policy.allow_network is True
    assert context.policy.allow_state_writes is False
    assert context.policy.allow_learning_writes is False
    assert context.policy.allow_external_actions is False


def test_replay_policy_blocks_network_and_all_mutations() -> None:
    context = DiscoveryExecutionContext.create(
        DiscoveryExecutionMode.REPLAY,
        uuid4(),
    )

    assert context.policy.allow_network is False
    assert context.policy.allow_state_writes is False
    assert context.policy.allow_learning_writes is False
    assert context.policy.allow_external_actions is False


def test_execution_context_generates_linkable_step_and_query_ids() -> None:
    context = DiscoveryExecutionContext.create(DiscoveryExecutionMode.DEBUG_TRACE)

    query_1 = context.next_query_id()
    step_1 = context.next_step_id("search")
    query_2 = context.next_query_id()
    step_2 = context.next_step_id("qualify community")

    assert query_1 != query_2
    assert step_1 != step_2
    assert query_1.startswith(f"{context.run_id}:query:")
    assert step_1.startswith(f"{context.run_id}:step:")
    assert step_2.endswith(":qualify_community")


def test_debug_trace_adapter_does_not_write_community_registry() -> None:
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    context = DiscoveryExecutionContext.create(
        DiscoveryExecutionMode.DEBUG_TRACE,
        product_id,
    )
    adapter = _TraceCommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=store,
        execution_context=context,
    )
    adapter._product_id = product_id  # noqa: SLF001

    adapter._remember_community(_candidate())  # noqa: SLF001

    assert store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE) == []


def test_runtime_effect_policy_can_persist_through_same_trace_adapter() -> None:
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    context = DiscoveryExecutionContext.create(
        DiscoveryExecutionMode.RUNTIME,
        product_id,
    )
    adapter = _TraceCommunityFirstTelegramDiscoveryAdapter(
        use_default_research_connector=False,
        store=store,
        execution_context=context,
    )
    adapter._product_id = product_id  # noqa: SLF001

    adapter._remember_community(_candidate())  # noqa: SLF001

    rows = store.list_namespace(TELEGRAM_COMMUNITY_REGISTRY_NAMESPACE)
    assert len(rows) == 1
    assert rows[0]["product_id"] == str(product_id)
