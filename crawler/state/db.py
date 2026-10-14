"""SQLite connection shared by all state tables."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

Params = Mapping[str, Any]


class Database:
    """One SQLite connection (WAL, autocommit) with small query helpers.

    Every helper takes SQL with named parameters (`:name`) and a dict of values.
    `transaction()` nests: only the outermost block commits, so table methods can be
    combined into one atomic operation by `StateStore`.
    """

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.clock = clock
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self._depth = 0

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")
        finally:
            self._depth = 0

    def execute(self, sql: str, params: Params | None = None) -> int:
        """Run a statement; returns the number of changed rows."""
        return self.conn.execute(sql, params or {}).rowcount

    def execute_many(self, sql: str, rows: list[Params]) -> int:
        before = self.conn.total_changes
        self.conn.executemany(sql, rows)
        return self.conn.total_changes - before

    def one(self, sql: str, params: Params | None = None) -> sqlite3.Row | None:
        return self.conn.execute(sql, params or {}).fetchone()

    def all(self, sql: str, params: Params | None = None) -> list[sqlite3.Row]:
        return self.conn.execute(sql, params or {}).fetchall()

    def scalar(self, sql: str, params: Params | None = None) -> Any:
        row = self.one(sql, params)
        return row[0] if row is not None else None
