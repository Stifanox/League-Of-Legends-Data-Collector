"""`meta` table: free-form key/value run metadata (e.g. last key error)."""

from __future__ import annotations

from crawler.state.db import Database

GET = "SELECT value FROM meta WHERE key = :key"

SET = """
    INSERT INTO meta (key, value) VALUES (:key, :value)
    ON CONFLICT (key) DO UPDATE SET value = excluded.value
"""


class Meta:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> str | None:
        return self._db.scalar(GET, {"key": key})

    def set(self, key: str, value: str | None) -> None:
        self._db.execute(SET, {"key": key, "value": value})
