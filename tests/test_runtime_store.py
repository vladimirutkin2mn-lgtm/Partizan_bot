from app.runtime_store import MemoryRuntimeStateStore


def test_memory_runtime_store_round_trip_and_namespace_clear() -> None:
    store = MemoryRuntimeStateStore()
    store.put("product", "one", {"name": "Oracle", "status": "CONFIRMED"})
    store.put("icp", "one", {"count": 10})

    assert store.get("product", "one") == {"name": "Oracle", "status": "CONFIRMED"}
    assert store.get("icp", "one") == {"count": 10}

    store.clear_namespace("product")
    assert store.get("product", "one") is None
    assert store.get("icp", "one") == {"count": 10}


def test_memory_runtime_store_returns_copy() -> None:
    store = MemoryRuntimeStateStore()
    store.put("test", "one", {"value": 1})
    payload = store.get("test", "one")
    assert payload is not None
    payload["value"] = 2
    assert store.get("test", "one") == {"value": 1}
