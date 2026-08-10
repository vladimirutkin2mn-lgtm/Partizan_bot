from uuid import uuid4

from sqlalchemy import inspect

from app.db import get_sync_engine
from app.runtime_store import DatabaseRuntimeStateStore


def test_database_runtime_store_round_trip() -> None:
    store = DatabaseRuntimeStateStore()
    namespace = f"ci-{uuid4()}"
    first_key = str(uuid4())
    second_key = str(uuid4())

    try:
        store.put(namespace, first_key, {"name": "Oracle", "count": 1})
        store.put(namespace, second_key, {"name": "Partizan", "count": 2})

        assert store.get(namespace, first_key) == {"name": "Oracle", "count": 1}
        rows = store.list_namespace(namespace)
        assert {row["name"] for row in rows} == {"Oracle", "Partizan"}

        store.delete(namespace, first_key)
        assert store.get(namespace, first_key) is None
        assert store.get(namespace, second_key) == {"name": "Partizan", "count": 2}
    finally:
        store.clear_namespace(namespace)


def test_migrations_create_channel_first_runtime_tables() -> None:
    table_names = set(inspect(get_sync_engine()).get_table_names())
    expected = {
        "runtime_snapshots",
        "distribution_plays",
        "distribution_experiments",
        "distribution_analytics_events",
        "distribution_experiment_spend",
        "distribution_growth_decisions",
        "distribution_learning_entries",
    }

    assert expected.issubset(table_names)
