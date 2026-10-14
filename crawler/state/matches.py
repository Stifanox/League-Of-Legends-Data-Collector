"""`matches` table: every match id seen, and how far it got (`queued | done | parked`)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from crawler.state.db import Database

IS_SEEN = "SELECT 1 FROM matches WHERE match_id = :match_id"

ENQUEUE = """
    INSERT OR IGNORE INTO matches
        (match_id, platform_id, seed_tier, seed_puuid, strategy, queued_at, updated_at)
    VALUES
        (:match_id, :platform_id, :seed_tier, :seed_puuid, :strategy, :now, :now)
"""

QUEUED = """
    SELECT * FROM matches
    WHERE platform_id = :platform_id AND status = 'queued'
    ORDER BY queued_at, match_id
    LIMIT :limit
"""

GET = "SELECT * FROM matches WHERE match_id = :match_id"

MARK_FETCHED = "UPDATE matches SET match_done = 1, updated_at = :now WHERE match_id = :match_id"

MARK_TIMELINE_FETCHED = "UPDATE matches SET timeline_done = 1, updated_at = :now WHERE match_id = :match_id"

MARK_DONE = "UPDATE matches SET status = 'done', updated_at = :now WHERE match_id = :match_id AND status != 'done'"

MARK_PARKED = "UPDATE matches SET status = 'parked', updated_at = :now WHERE match_id = :match_id"

REQUEUE = "UPDATE matches SET status = 'queued', updated_at = :now WHERE match_id = :match_id AND status = 'parked'"

COUNT_BY_STATUS = "SELECT status, COUNT(*) FROM matches WHERE platform_id = :platform_id GROUP BY status"

COUNT_TIMELINES = "SELECT COUNT(*) FROM matches WHERE platform_id = :platform_id AND timeline_done = 1"

PLATFORMS = "SELECT DISTINCT platform_id FROM matches"


@dataclass(frozen=True)
class MatchRow:
    match_id: str
    platform_id: str
    status: str
    match_done: bool
    timeline_done: bool
    seed_tier: str | None
    seed_puuid: str | None
    strategy: str | None


class Matches:
    def __init__(self, db: Database) -> None:
        self._db = db

    def is_seen(self, match_id: str) -> bool:
        return self._db.one(IS_SEEN, {"match_id": match_id}) is not None

    def enqueue(
        self,
        platform_id: str,
        match_ids: Iterable[str],
        *,
        seed_tier: str | None,
        seed_puuid: str | None,
        strategy: str,
    ) -> list[str]:
        """Queue ids not seen before; returns the newly queued ones in input order."""
        now = self._db.clock()
        new = []
        with self._db.transaction():
            for match_id in match_ids:
                params = {
                    "match_id": match_id,
                    "platform_id": platform_id,
                    "seed_tier": seed_tier,
                    "seed_puuid": seed_puuid,
                    "strategy": strategy,
                    "now": now,
                }
                if self._db.execute(ENQUEUE, params):
                    new.append(match_id)
        return new

    def queued(self, platform_id: str, limit: int = 100) -> list[MatchRow]:
        """Oldest queued first. A match left half-done by a crash is still queued."""
        return [_row(r) for r in self._db.all(QUEUED, {"platform_id": platform_id, "limit": limit})]

    def get(self, match_id: str) -> MatchRow | None:
        row = self._db.one(GET, {"match_id": match_id})
        return _row(row) if row is not None else None

    def mark_fetched(self, match_id: str) -> None:
        self._db.execute(MARK_FETCHED, {"match_id": match_id, "now": self._db.clock()})

    def mark_timeline_fetched(self, match_id: str) -> None:
        self._db.execute(MARK_TIMELINE_FETCHED, {"match_id": match_id, "now": self._db.clock()})

    def mark_done(self, match_id: str) -> bool:
        """Returns False if the match was already done (or is unknown)."""
        return bool(self._db.execute(MARK_DONE, {"match_id": match_id, "now": self._db.clock()}))

    def mark_parked(self, match_id: str) -> None:
        self._db.execute(MARK_PARKED, {"match_id": match_id, "now": self._db.clock()})

    def requeue(self, match_id: str) -> None:
        """Put a parked match back in the queue."""
        self._db.execute(REQUEUE, {"match_id": match_id, "now": self._db.clock()})

    def count_by_status(self, platform_id: str) -> dict[str, int]:
        return {status: n for status, n in self._db.all(COUNT_BY_STATUS, {"platform_id": platform_id})}

    def count_timelines(self, platform_id: str) -> int:
        return self._db.scalar(COUNT_TIMELINES, {"platform_id": platform_id})

    def platforms(self) -> set[str]:
        return {row[0] for row in self._db.all(PLATFORMS)}


def _row(row: sqlite3.Row) -> MatchRow:
    return MatchRow(
        match_id=row["match_id"],
        platform_id=row["platform_id"],
        status=row["status"],
        match_done=bool(row["match_done"]),
        timeline_done=bool(row["timeline_done"]),
        seed_tier=row["seed_tier"],
        seed_puuid=row["seed_puuid"],
        strategy=row["strategy"],
    )
