"""`tier_progress` table: matches collected per platform and tier, exhausted flags, apex rounds."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from crawler.state.db import Database
from riot_client.routing import TIERS

TIER_ORDER = {tier: i for i, tier in enumerate(TIERS)}

ENSURE = "INSERT OR IGNORE INTO tier_progress (platform_id, tier) VALUES (:platform_id, :tier)"

LIST = "SELECT * FROM tier_progress WHERE platform_id = :platform_id"

ADD_COLLECTED = """
    INSERT INTO tier_progress (platform_id, tier, collected) VALUES (:platform_id, :tier, :n)
    ON CONFLICT (platform_id, tier) DO UPDATE SET collected = collected + excluded.collected
"""

SET_EXHAUSTED = """
    INSERT INTO tier_progress (platform_id, tier, exhausted) VALUES (:platform_id, :tier, :exhausted)
    ON CONFLICT (platform_id, tier) DO UPDATE SET exhausted = excluded.exhausted
"""

BUMP_APEX_ROUND = """
    INSERT INTO tier_progress (platform_id, tier, apex_round) VALUES (:platform_id, :tier, 1)
    ON CONFLICT (platform_id, tier) DO UPDATE SET apex_round = apex_round + 1
"""

APEX_ROUND = "SELECT apex_round FROM tier_progress WHERE platform_id = :platform_id AND tier = :tier"

PLATFORMS = "SELECT DISTINCT platform_id FROM tier_progress"


@dataclass(frozen=True)
class TierProgress:
    tier: str
    collected: int
    exhausted: bool
    apex_round: int


class Tiers:
    def __init__(self, db: Database) -> None:
        self._db = db

    def ensure(self, platform_id: str, tiers: Iterable[str] = TIERS) -> None:
        """Create rows for the tiers of a platform (existing progress is kept)."""
        with self._db.transaction():
            self._db.execute_many(ENSURE, [{"platform_id": platform_id, "tier": t} for t in tiers])

    def progress(self, platform_id: str) -> list[TierProgress]:
        """Progress per tier, lowest tier first."""
        rows = self._db.all(LIST, {"platform_id": platform_id})
        progress = [TierProgress(r["tier"], r["collected"], bool(r["exhausted"]), r["apex_round"]) for r in rows]
        return sorted(progress, key=lambda p: _order(p.tier))

    def least_collected(self, platform_id: str) -> str | None:
        """Non-exhausted tier with the fewest collected matches (ties: lower tier first)."""
        candidates = [p for p in self.progress(platform_id) if not p.exhausted]
        if not candidates:
            return None
        return min(candidates, key=lambda p: (p.collected, _order(p.tier))).tier

    def add_collected(self, platform_id: str, tier: str, n: int = 1) -> None:
        self._db.execute(ADD_COLLECTED, {"platform_id": platform_id, "tier": tier, "n": n})

    def set_exhausted(self, platform_id: str, tier: str, exhausted: bool = True) -> None:
        self._db.execute(SET_EXHAUSTED, {"platform_id": platform_id, "tier": tier, "exhausted": int(exhausted)})

    def bump_apex_round(self, platform_id: str, tier: str) -> int:
        """Start the next deep-paging round of an apex tier; returns the new round number."""
        params = {"platform_id": platform_id, "tier": tier}
        with self._db.transaction():
            self._db.execute(BUMP_APEX_ROUND, params)
            return self._db.scalar(APEX_ROUND, params)

    def platforms(self) -> set[str]:
        return {row[0] for row in self._db.all(PLATFORMS)}


def _order(tier: str) -> int:
    return TIER_ORDER.get(tier, len(TIER_ORDER))
