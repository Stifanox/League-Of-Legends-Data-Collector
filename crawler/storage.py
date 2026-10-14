"""Write order for crawled data (DESIGN §9): raw append + flush first, then state.

A crash between the two leaves at most one duplicate raw line (deduped by the export);
it can never mark something done that is not on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from crawler.raw_writer import RawWriter, match_record, rank_record, timeline_record
from crawler.state import StateStore
from riot_client.endpoints.league import solo_queue_rank


class Storage:
    """Raw writers for matches/timelines/ranks plus the state store."""

    def __init__(self, data_dir: Path, *, roll_mb: float = 128, fsync: bool = True) -> None:
        self.data_dir = data_dir
        self.state = StateStore(data_dir / "state.db")
        raw = data_dir / "raw"
        self.matches = RawWriter(raw, "matches", roll_mb, fsync=fsync)
        self.timelines = RawWriter(raw, "timelines", roll_mb, fsync=fsync)
        self.ranks = RawWriter(raw, "ranks", roll_mb, fsync=fsync)

    def persist_match(
        self,
        platform_id: str,
        match_id: str,
        data: dict[str, Any],
        *,
        seed_tier: str | None,
        seed_puuid: str | None,
        strategy: str,
    ) -> None:
        """Store a fetched match; it must have been queued with `state.matches.enqueue` first."""
        record = match_record(
            match_id, platform_id, data, seed_tier=seed_tier, seed_puuid=seed_puuid, strategy=strategy
        )
        self.matches.write(platform_id, record)
        self.state.matches.mark_fetched(match_id)

    def persist_timeline(self, platform_id: str, match_id: str, data: dict[str, Any]) -> None:
        self.timelines.write(platform_id, timeline_record(match_id, platform_id, data))
        self.state.matches.mark_timeline_fetched(match_id)

    def persist_rank(self, platform_id: str, puuid: str, entries: list[dict[str, Any]]) -> str:
        """Store a league-v4 by-puuid response; returns `RANKED` or `UNRANKED`."""
        status = solo_queue_rank(entries)["status"]
        self.ranks.write(platform_id, rank_record(puuid, platform_id, status, entries))
        self.state.record_rank_lookup(platform_id, puuid, status)
        return status

    def close(self) -> None:
        """Close raw files and the database (idempotent)."""
        for writer in (self.matches, self.timelines, self.ranks):
            writer.close()
        self.state.close()

    def __enter__(self) -> Storage:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
