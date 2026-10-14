"""`players` table: seed players per platform, whether they were visited, and their paging offset."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from crawler.state.db import Database

ADD = """
    INSERT OR IGNORE INTO players (platform_id, puuid, tier, source, added_at)
    VALUES (:platform_id, :puuid, :tier, :source, :now)
"""

IS_KNOWN = "SELECT 1 FROM players WHERE platform_id = :platform_id AND puuid = :puuid"

NEXT_UNVISITED = """
    SELECT * FROM players
    WHERE platform_id = :platform_id
      AND (:tier IS NULL OR tier = :tier)
      AND visited_at IS NULL
      AND exhausted = 0
    ORDER BY added_at, rowid
    LIMIT 1
"""

RANDOM_UNVISITED = """
    SELECT * FROM players
    WHERE platform_id = :platform_id
      AND (:tier IS NULL OR tier = :tier)
      AND visited_at IS NULL
      AND exhausted = 0
    ORDER BY RANDOM()
    LIMIT 1
"""

NEXT_REVISIT = """
    SELECT * FROM players
    WHERE platform_id = :platform_id
      AND tier = :tier
      AND visited_at IS NOT NULL
      AND exhausted = 0
    ORDER BY visited_at, rowid
    LIMIT 1
"""

MARK_VISITED = """
    UPDATE players
    SET visited_at = :now, visits = visits + 1, next_start = :next_start, exhausted = :exhausted
    WHERE platform_id = :platform_id AND puuid = :puuid
"""

COUNT = """
    SELECT COUNT(*) FROM players
    WHERE platform_id = :platform_id
      AND (:tier IS NULL OR tier = :tier)
      AND (:unvisited = 0 OR (visited_at IS NULL AND exhausted = 0))
"""

COUNT_VISITED = "SELECT COUNT(*) FROM players WHERE platform_id = :platform_id AND visited_at IS NOT NULL"

PLATFORMS = "SELECT DISTINCT platform_id FROM players"


@dataclass(frozen=True)
class Player:
    platform_id: str
    puuid: str
    tier: str | None
    source: str
    visits: int
    next_start: int


class Players:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, platform_id: str, puuids: Iterable[str], *, tier: str | None, source: str) -> list[str]:
        """Add players not known yet; returns the newly added ones."""
        now = self._db.clock()
        new = []
        with self._db.transaction():
            for puuid in puuids:
                params = {"platform_id": platform_id, "puuid": puuid, "tier": tier, "source": source, "now": now}
                if self._db.execute(ADD, params):
                    new.append(puuid)
        return new

    def known(self, platform_id: str, puuids: Iterable[str]) -> set[str]:
        return {p for p in puuids if self._db.one(IS_KNOWN, {"platform_id": platform_id, "puuid": p}) is not None}

    def next_unvisited(self, platform_id: str, tier: str | None = None, *, random: bool = False) -> Player | None:
        """A never-visited, non-exhausted player: oldest added first, or a random one."""
        row = self._db.one(RANDOM_UNVISITED if random else NEXT_UNVISITED, {"platform_id": platform_id, "tier": tier})
        return _row(row) if row is not None else None

    def next_revisit(self, platform_id: str, tier: str) -> Player | None:
        """A visited, non-exhausted player of `tier`, least recently visited first (apex deep paging)."""
        row = self._db.one(NEXT_REVISIT, {"platform_id": platform_id, "tier": tier})
        return _row(row) if row is not None else None

    def mark_visited(self, platform_id: str, puuid: str, *, next_start: int, exhausted: bool = False) -> None:
        params = {
            "platform_id": platform_id,
            "puuid": puuid,
            "next_start": next_start,
            "exhausted": int(exhausted),
            "now": self._db.clock(),
        }
        self._db.execute(MARK_VISITED, params)

    def count(self, platform_id: str, tier: str | None = None, *, unvisited: bool = False) -> int:
        return self._db.scalar(COUNT, {"platform_id": platform_id, "tier": tier, "unvisited": int(unvisited)})

    def count_visited(self, platform_id: str) -> int:
        return self._db.scalar(COUNT_VISITED, {"platform_id": platform_id})

    def platforms(self) -> set[str]:
        return {row[0] for row in self._db.all(PLATFORMS)}


def _row(row: sqlite3.Row) -> Player:
    return Player(
        platform_id=row["platform_id"],
        puuid=row["puuid"],
        tier=row["tier"],
        source=row["source"],
        visits=row["visits"],
        next_start=row["next_start"],
    )
