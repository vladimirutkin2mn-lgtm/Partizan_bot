from functools import lru_cache
from typing import Protocol

from sqlalchemy import delete, select

from app.config import get_settings
from app.db import get_sync_session_factory
from app.runtime_models import RuntimeSnapshot


class RuntimeStateStore(Protocol):
    ephemeral: bool

    def get(self, namespace: str, entity_key: str) -> dict | None: ...

    def put(self, namespace: str, entity_key: str, payload: dict) -> None: ...

    def delete(self, namespace: str, entity_key: str) -> None: ...

    def clear_namespace(self, namespace: str) -> None: ...

    def list_namespace(self, namespace: str) -> list[dict]: ...


class MemoryRuntimeStateStore:
    ephemeral = True

    def __init__(self) -> None:
        self._payloads: dict[tuple[str, str], dict] = {}

    def get(self, namespace: str, entity_key: str) -> dict | None:
        payload = self._payloads.get((namespace, entity_key))
        return dict(payload) if payload is not None else None

    def put(self, namespace: str, entity_key: str, payload: dict) -> None:
        self._payloads[(namespace, entity_key)] = dict(payload)

    def delete(self, namespace: str, entity_key: str) -> None:
        self._payloads.pop((namespace, entity_key), None)

    def clear_namespace(self, namespace: str) -> None:
        keys = [key for key in self._payloads if key[0] == namespace]
        for key in keys:
            self._payloads.pop(key, None)

    def list_namespace(self, namespace: str) -> list[dict]:
        rows = [
            (entity_key, payload)
            for (row_namespace, entity_key), payload in self._payloads.items()
            if row_namespace == namespace
        ]
        rows.sort(key=lambda item: item[0])
        return [dict(payload) for _, payload in rows]


class DatabaseRuntimeStateStore:
    ephemeral = False

    def get(self, namespace: str, entity_key: str) -> dict | None:
        session_factory = get_sync_session_factory()
        with session_factory() as session:
            row = session.get(
                RuntimeSnapshot,
                {"namespace": namespace, "entity_key": entity_key},
            )
            return dict(row.payload) if row is not None else None

    def put(self, namespace: str, entity_key: str, payload: dict) -> None:
        session_factory = get_sync_session_factory()
        with session_factory.begin() as session:
            row = session.get(
                RuntimeSnapshot,
                {"namespace": namespace, "entity_key": entity_key},
            )
            if row is None:
                session.add(
                    RuntimeSnapshot(
                        namespace=namespace,
                        entity_key=entity_key,
                        payload=payload,
                    )
                )
            else:
                row.payload = payload

    def delete(self, namespace: str, entity_key: str) -> None:
        session_factory = get_sync_session_factory()
        with session_factory.begin() as session:
            row = session.get(
                RuntimeSnapshot,
                {"namespace": namespace, "entity_key": entity_key},
            )
            if row is not None:
                session.delete(row)

    def clear_namespace(self, namespace: str) -> None:
        session_factory = get_sync_session_factory()
        with session_factory.begin() as session:
            session.execute(
                delete(RuntimeSnapshot).where(RuntimeSnapshot.namespace == namespace)
            )

    def list_namespace(self, namespace: str) -> list[dict]:
        session_factory = get_sync_session_factory()
        with session_factory() as session:
            rows = session.scalars(
                select(RuntimeSnapshot)
                .where(RuntimeSnapshot.namespace == namespace)
                .order_by(RuntimeSnapshot.entity_key)
            ).all()
            return [dict(row.payload) for row in rows]


@lru_cache
def get_runtime_store() -> RuntimeStateStore:
    mode = get_settings().runtime_storage.strip().lower()
    if mode == "memory":
        return MemoryRuntimeStateStore()
    if mode == "database":
        return DatabaseRuntimeStateStore()
    raise ValueError("RUNTIME_STORAGE must be either 'memory' or 'database'")
