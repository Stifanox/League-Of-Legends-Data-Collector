"""Rank tables: `rank_queue` (players waiting for a lookup) and `rank_lookups` (lookups done)."""

from __future__ import annotations

from collections.abc import Iterable

from crawler.state.db import Database

# ----------------------------------------------------------------- rank_queue

QUEUE_ADD = """
    INSERT OR IGNORE INTO rank_queue (platform_id, puuid, enqueued_at)
    VALUES (:platform_id, :puuid, :now)
"""

QUEUE_LIST = """
    SELECT puuid FROM rank_queue
    WHERE platform_id = :platform_id
    ORDER BY enqueued_at, rowid
    LIMIT :limit
"""

QUEUE_REMOVE = "DELETE FROM rank_queue WHERE platform_id = :platform_id AND puuid = :puuid"

QUEUE_COUNT = "SELECT COUNT(*) FROM rank_queue WHERE platform_id = :platform_id"

# ----------------------------------------------------------------- rank_lookups

LOOKUP_ADD = """
    INSERT INTO rank_lookups (platform_id, puuid, fetched_at, status)
    VALUES (:platform_id, :puuid, :fetched_at, :status)
"""

LOOKUP_RECENT = """
    SELECT 1 FROM rank_lookups
    WHERE platform_id = :platform_id AND puuid = :puuid AND fetched_at >= :since
    LIMIT 1
"""

LOOKUP_COUNT = "SELECT COUNT(*) FROM rank_lookups WHERE platform_id = :platform_id"


class RankQueue:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, platform_id: str, puuids: Iterable[str]) -> int:
        """Queue players for a rank lookup; returns how many were not queued already."""
        now = self._db.clock()
        with self._db.transaction():
            return self._db.execute_many(QUEUE_ADD, [{"platform_id": platform_id, "puuid": p, "now": now} for p in puuids])

    def next(self, platform_id: str, limit: int = 100) -> list[str]:
        """Oldest queued players first."""
        return [row[0] for row in self._db.all(QUEUE_LIST, {"platform_id": platform_id, "limit": limit})]

    def remove(self, platform_id: str, puuid: str) -> None:
        self._db.execute(QUEUE_REMOVE, {"platform_id": platform_id, "puuid": puuid})

    def count(self, platform_id: str) -> int:
        return self._db.scalar(QUEUE_COUNT, {"platform_id": platform_id})


class RankLookups:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, platform_id: str, puuid: str, status: str, fetched_at: float | None = None) -> None:
        params = {
            "platform_id": platform_id,
            "puuid": puuid,
            "status": status,
            "fetched_at": fetched_at if fetched_at is not None else self._db.clock(),
        }
        self._db.execute(LOOKUP_ADD, params)

    def recent(self, platform_id: str, puuid: str, within_hours: float) -> bool:
        """True if the player was looked up within `within_hours`."""
        since = self._db.clock() - within_hours * 3600
        return self._db.one(LOOKUP_RECENT, {"platform_id": platform_id, "puuid": puuid, "since": since}) is not None

    def count(self, platform_id: str) -> int:
        return self._db.scalar(LOOKUP_COUNT, {"platform_id": platform_id})
