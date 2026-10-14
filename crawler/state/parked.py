"""`parked` table: items that failed after all retries, kept for a later retry."""

from __future__ import annotations

from dataclasses import dataclass

from crawler.state.db import Database

KINDS = ("match", "timeline", "rank", "ids", "league")

UPSERT = """
    INSERT INTO parked (kind, platform_id, key, error, status_code, first_parked, last_attempt)
    VALUES (:kind, :platform_id, :key, :error, :status_code, :now, :now)
    ON CONFLICT (kind, platform_id, key) DO UPDATE SET
        error = excluded.error,
        status_code = excluded.status_code,
        attempts = attempts + 1,
        last_attempt = excluded.last_attempt
"""

REMOVE = "DELETE FROM parked WHERE kind = :kind AND platform_id = :platform_id AND key = :key"

LIST = """
    SELECT * FROM parked
    WHERE (:kind IS NULL OR kind = :kind)
      AND (:platform_id IS NULL OR platform_id = :platform_id)
    ORDER BY last_attempt
    LIMIT :limit
"""

COUNT_BY_KIND = "SELECT kind, COUNT(*) FROM parked GROUP BY kind"


@dataclass(frozen=True)
class ParkedItem:
    kind: str
    platform_id: str
    key: str
    error: str | None
    status_code: int | None
    attempts: int
    last_attempt: float


class Parked:
    def __init__(self, db: Database) -> None:
        self._db = db

    def upsert(self, kind: str, platform_id: str, key: str, error: str, status_code: int | None = None) -> None:
        """Park an item, or count one more failed attempt if it is parked already."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, got {kind!r}")
        params = {
            "kind": kind,
            "platform_id": platform_id,
            "key": key,
            "error": error,
            "status_code": status_code,
            "now": self._db.clock(),
        }
        self._db.execute(UPSERT, params)

    def remove(self, kind: str, platform_id: str, key: str) -> None:
        self._db.execute(REMOVE, {"kind": kind, "platform_id": platform_id, "key": key})

    def items(self, kind: str | None = None, platform_id: str | None = None, limit: int = 100) -> list[ParkedItem]:
        """Parked items, least recently attempted first."""
        rows = self._db.all(LIST, {"kind": kind, "platform_id": platform_id, "limit": limit})
        return [
            ParkedItem(
                kind=r["kind"],
                platform_id=r["platform_id"],
                key=r["key"],
                error=r["error"],
                status_code=r["status_code"],
                attempts=r["attempts"],
                last_attempt=r["last_attempt"],
            )
            for r in rows
        ]

    def count_by_kind(self) -> dict[str, int]:
        return {kind: n for kind, n in self._db.all(COUNT_BY_KIND)}
