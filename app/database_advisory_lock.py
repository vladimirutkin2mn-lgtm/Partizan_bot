from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text

from app.db import get_sync_engine


@contextmanager
def postgres_session_advisory_lock(lock_key: int) -> Iterator[bool]:
    """Try to hold one PostgreSQL session advisory lock for the context lifetime.

    A dedicated SQLAlchemy connection stays checked out while the caller owns
    the lock. The lock is explicitly released before that connection returns to
    the pool so a pooled session can never retain an autonomous-execution lock.
    """

    with get_sync_engine().connect() as connection:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_key)"),
                {"lock_key": lock_key},
            ).scalar_one()
        )
        try:
            yield acquired
        finally:
            if acquired:
                released = bool(
                    connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": lock_key},
                    ).scalar_one()
                )
                if not released:
                    connection.invalidate()
                    raise RuntimeError("PostgreSQL advisory lock release failed")
