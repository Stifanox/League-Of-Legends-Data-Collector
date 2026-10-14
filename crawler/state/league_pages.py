"""`league_pages` table: which league-v4 entry pages were fetched and how many players they had."""

from __future__ import annotations

from crawler.state.db import Database

RECORD = """
    INSERT INTO league_pages (platform_id, tier, division, page, players, fetched_at)
    VALUES (:platform_id, :tier, :division, :page, :players, :now)
    ON CONFLICT (platform_id, tier, division, page) DO UPDATE SET
        players = excluded.players,
        fetched_at = excluded.fetched_at
"""

LIST = """
    SELECT page, players FROM league_pages
    WHERE platform_id = :platform_id AND tier = :tier AND division = :division
"""


class LeaguePages:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(self, platform_id: str, tier: str, division: str, page: int, players: int) -> None:
        params = {
            "platform_id": platform_id,
            "tier": tier,
            "division": division,
            "page": page,
            "players": players,
            "now": self._db.clock(),
        }
        self._db.execute(RECORD, params)

    def pages(self, platform_id: str, tier: str, division: str) -> dict[int, int]:
        """`page -> number of players` for pages fetched so far (0 = empty page)."""
        rows = self._db.all(LIST, {"platform_id": platform_id, "tier": tier, "division": division})
        return {page: players for page, players in rows}
