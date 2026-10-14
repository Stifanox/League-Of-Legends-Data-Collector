"""`StateStore`: opens `state.db` and combines the tables into crawler-level operations."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from crawler.state.db import Database
from crawler.state.league_pages import LeaguePages
from crawler.state.matches import Matches
from crawler.state.meta import Meta
from crawler.state.parked import Parked
from crawler.state.players import Players
from crawler.state.ranks import RankLookups, RankQueue
from crawler.state.schema import migrate, schema_version
from crawler.state.tiers import Tiers


class StateStore:
    """Crawl state (DESIGN §9). One instance per process; not thread-safe.

    Single-table work goes through the table objects (`state.matches.enqueue(...)`);
    operations that must change several tables atomically are methods here.
    """

    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time) -> None:
        self.db = Database(path, clock=clock)
        migrate(self.db)
        self.meta = Meta(self.db)
        self.matches = Matches(self.db)
        self.players = Players(self.db)
        self.tiers = Tiers(self.db)
        self.league_pages = LeaguePages(self.db)
        self.rank_queue = RankQueue(self.db)
        self.rank_lookups = RankLookups(self.db)
        self.parked = Parked(self.db)

    @property
    def schema_version(self) -> int:
        return schema_version(self.db)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ----------------------------------------------------------------- multi-table operations

    def finish_match(self, match_id: str) -> bool:
        """Mark a match done, clear its parked entries and count it for its seed tier (once).

        Returns False if it was already done or is unknown.
        """
        with self.db.transaction():
            match = self.matches.get(match_id)
            if match is None or not self.matches.mark_done(match_id):
                return False
            self.parked.remove("match", match.platform_id, match_id)
            self.parked.remove("timeline", match.platform_id, match_id)
            if match.seed_tier:
                self.tiers.add_collected(match.platform_id, match.seed_tier)
        return True

    def park(self, kind: str, platform_id: str, key: str, error: str, status_code: int | None = None) -> None:
        """Park a failed item. A parked match/timeline also parks the match; a parked rank leaves the queue."""
        with self.db.transaction():
            self.parked.upsert(kind, platform_id, key, error, status_code)
            if kind in ("match", "timeline"):
                self.matches.mark_parked(key)
            elif kind == "rank":
                self.rank_queue.remove(platform_id, key)

    def record_rank_lookup(self, platform_id: str, puuid: str, status: str, fetched_at: float | None = None) -> None:
        """Store a successful lookup (`RANKED`/`UNRANKED`) and take the player off the queue and parking."""
        with self.db.transaction():
            self.rank_lookups.add(platform_id, puuid, status, fetched_at)
            self.rank_queue.remove(platform_id, puuid)
            self.parked.remove("rank", platform_id, puuid)

    # ----------------------------------------------------------------- summary

    def platforms(self) -> list[str]:
        return sorted(self.matches.platforms() | self.players.platforms() | self.tiers.platforms())

    def summary(self) -> dict[str, object]:
        """Counts for `python -m crawler status`."""
        per_platform: dict[str, dict[str, object]] = {}
        for platform_id in self.platforms():
            statuses = self.matches.count_by_status(platform_id)
            per_platform[platform_id] = {
                "matches_done": statuses.get("done", 0),
                "matches_queued": statuses.get("queued", 0),
                "matches_parked": statuses.get("parked", 0),
                "timelines_done": self.matches.count_timelines(platform_id),
                "players": self.players.count(platform_id),
                "players_visited": self.players.count_visited(platform_id),
                "rank_lookups": self.rank_lookups.count(platform_id),
                "rank_queue": self.rank_queue.count(platform_id),
                "tiers": self.tiers.progress(platform_id),
            }
        return {"platforms": per_platform, "parked": self.parked.count_by_kind()}
