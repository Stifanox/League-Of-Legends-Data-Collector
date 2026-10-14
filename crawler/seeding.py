"""Choosing whose matches to fetch next (DESIGN §8).

`StratifiedSeeder` balances matches across tiers: it always works on the non-exhausted tier
with the fewest collected matches. Below MASTER it samples random players from random
league pages; for MASTER+ it takes the whole league and, once every player was visited,
pages deeper into their match history. `SnowballSeeder` starts from one Riot ID and
continues with participants of collected matches.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from crawler.state import StateStore
from riot_client.endpoints import JsonGetter, account, league
from riot_client.routing import APEX_TIERS, DIVISIONS, account_region_of

log = logging.getLogger(__name__)

# Players added per fetched league page (a few per page spreads the sample over many pages).
SEEDS_PER_PAGE = 5


@dataclass(frozen=True)
class Seed:
    """A player to fetch match ids for, starting at offset `start` of their history."""

    puuid: str
    tier: str | None
    start: int = 0


class StratifiedSeeder:
    strategy = "stratified"

    def __init__(
        self,
        client: JsonGetter,
        state: StateStore,
        platform: str,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self.client = client
        self.state = state
        self.platform = platform.lower()
        self.platform_id = platform.upper()
        self.rng = rng or random.Random()

    async def prepare(self) -> None:
        self.state.tiers.ensure(self.platform_id)

    async def next_seed(self) -> Seed | None:
        """Next player to visit, or None when every tier of this platform is exhausted."""
        while True:
            tier = self.state.tiers.least_collected(self.platform_id)
            if tier is None:
                return None
            seed = await (self._apex_seed(tier) if tier in APEX_TIERS else self._division_seed(tier))
            if seed is not None:
                return seed
            log.info("%s %s: no more players - tier exhausted", self.platform_id, tier)
            self.state.tiers.set_exhausted(self.platform_id, tier)

    # ----------------------------------------------------------------- IRON..DIAMOND

    async def _division_seed(self, tier: str) -> Seed | None:
        while True:
            player = self.state.players.next_unvisited(self.platform_id, tier, random=True)
            if player is not None:
                return Seed(player.puuid, tier, player.next_start)
            choice = self._pick_page(tier)
            if choice is None:
                return None  # every division fully explored and every player visited
            division, page = choice
            entries = await league.entries_page(self.client, self.platform, tier, division, page)
            puuids = league.league_puuids(entries)
            self.state.league_pages.record(self.platform_id, tier, division, page, len(puuids))
            known = self.state.players.known(self.platform_id, puuids)
            fresh = [p for p in puuids if p not in known]
            picked = self.rng.sample(fresh, min(SEEDS_PER_PAGE, len(fresh)))
            self.state.players.add(self.platform_id, picked, tier=tier, source="league")
            log.debug("%s %s %s page %d: %d players, %d new seeds", self.platform_id, tier, division, page, len(puuids), len(picked))

    def _pick_page(self, tier: str) -> tuple[str, int] | None:
        """A random not-yet-fetched page of a random division that may still have pages."""
        options = []
        for division in DIVISIONS:
            pages = self.state.league_pages.pages(self.platform_id, tier, division)
            candidates = candidate_pages(pages)
            if candidates:
                options.append((division, candidates))
        if not options:
            return None
        division, candidates = self.rng.choice(options)
        return division, self.rng.choice(candidates)

    # ----------------------------------------------------------------- MASTER+

    async def _apex_seed(self, tier: str) -> Seed | None:
        player = self.state.players.next_unvisited(self.platform_id, tier)
        if player is None and self._apex_round(tier) == 0:
            await self._load_apex_league(tier)
            player = self.state.players.next_unvisited(self.platform_id, tier)
        if player is not None:
            return Seed(player.puuid, tier, player.next_start)
        # Everyone was visited once: page deeper into their histories.
        player = self.state.players.next_revisit(self.platform_id, tier)
        if player is not None:
            return Seed(player.puuid, tier, player.next_start)
        # Every player ran out of in-window matches: look for players new to the league once more.
        if await self._load_apex_league(tier):
            player = self.state.players.next_unvisited(self.platform_id, tier)
            return Seed(player.puuid, tier, player.next_start) if player else None
        return None

    async def _load_apex_league(self, tier: str) -> int:
        data = await league.apex_league(self.client, self.platform, tier)
        new = self.state.players.add(self.platform_id, league.league_puuids(data), tier=tier, source="apex")
        round_ = self.state.tiers.bump_apex_round(self.platform_id, tier)
        log.info("%s %s league round %d: %d new players", self.platform_id, tier, round_, len(new))
        return len(new)

    def _apex_round(self, tier: str) -> int:
        return next((p.apex_round for p in self.state.tiers.progress(self.platform_id) if p.tier == tier), 0)


def candidate_pages(pages: dict[int, int]) -> list[int]:
    """Pages worth fetching for one division, given `page -> players` fetched so far.

    Pages below the first known empty page that were not fetched yet. While no empty page is
    known, the range grows to twice the highest page fetched (+2), so exploration doubles
    until the end of the division is found.
    """
    empty = [page for page, players in pages.items() if players == 0]
    upper = min(empty) if empty else max(pages, default=0) * 2 + 2
    return [page for page in range(1, upper) if page not in pages]


class SnowballSeeder:
    """Starts at `SEED_RIOT_ID`, then picks random unvisited participants of collected matches."""

    strategy = "snowball"

    def __init__(self, client: JsonGetter, state: StateStore, platform: str, seed_riot_id: str) -> None:
        self.client = client
        self.state = state
        self.platform = platform.lower()
        self.platform_id = platform.upper()
        self.seed_riot_id = seed_riot_id

    async def prepare(self) -> None:
        if self.state.players.count(self.platform_id):
            return
        game_name, tag_line = account.parse_riot_id(self.seed_riot_id)
        acc = await account.account_by_riot_id(self.client, account_region_of(self.platform), game_name, tag_line)
        self.state.players.add(self.platform_id, [acc["puuid"]], tier=None, source="seed")
        log.info("%s: snowball seed %s", self.platform_id, self.seed_riot_id)

    async def next_seed(self) -> Seed | None:
        player = self.state.players.next_unvisited(self.platform_id, random=True)
        return Seed(player.puuid, None, player.next_start) if player else None
