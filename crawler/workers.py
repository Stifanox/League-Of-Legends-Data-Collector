"""Per-platform workers (DESIGN §7, §8): one fetches matches, one looks up ranks.

Both are resumable: everything they need to continue lives in `state.db`, and a match or
rank is only marked done after its raw record is on disk.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from crawler.config import Settings
from crawler.seeding import Seed
from crawler.shutdown import ShutdownController
from crawler.state import MatchRow
from crawler.storage import Storage
from riot_client.endpoints import JsonGetter, league, match
from riot_client.errors import BadRequest, Forbidden, NotFound, RiotApiError
from riot_client.routing import region_of

log = logging.getLogger(__name__)

# Errors that will not change on retry: logged and parked without automatic retries.
PERMANENT_ERRORS = (BadRequest, Forbidden, NotFound)
PERMANENT_STATUS = (400, 403, 404)

# Automatic retry of parked items, from the match worker loop.
RETRY_PARKED_EVERY_SEEDS = 25
RETRY_PARKED_BATCH = 5
RETRY_PARKED_MIN_AGE = 15 * 60
RETRY_PARKED_MAX_ATTEMPTS = 5

# Pause after a failed seeding call (league page / apex league / account), doubling up to the cap.
SEED_ERROR_PAUSE = 30.0
SEED_ERROR_PAUSE_MAX = 600.0

RANK_BATCH = 50
RANK_IDLE_POLL = 5.0


@dataclass
class PlatformStats:
    """Counters of the current run for one platform (shown by the progress UI)."""

    matches: int = 0
    timelines: int = 0
    ranks: int = 0
    rank_status: Counter = field(default_factory=Counter)
    parked: int = 0
    seeds: int = 0
    current_tier: str | None = None
    finished: bool = False


@dataclass
class CrawlContext:
    """Everything the workers share."""

    settings: Settings
    client: JsonGetter
    storage: Storage
    shutdown: ShutdownController
    disk_ok: asyncio.Event = field(default_factory=asyncio.Event)
    max_matches: int | None = None
    stats: dict[str, PlatformStats] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.disk_ok.set()

    @property
    def total_matches(self) -> int:
        return sum(s.matches for s in self.stats.values())

    @property
    def total_ranks(self) -> int:
        return sum(s.ranks for s in self.stats.values())

    def target_reached(self) -> bool:
        return self.max_matches is not None and self.total_matches >= self.max_matches

    def window(self) -> tuple[int, int]:
        """`(start_time, end_time)` in epoch seconds for match id queries (sliding window)."""
        end = int(time.time())
        return end - self.settings.window_days * 86400, end

    async def ready(self) -> bool:
        """Wait while the disk is low; False when the crawler is stopping."""
        if not self.disk_ok.is_set():
            stop = asyncio.ensure_future(self.shutdown.event.wait())
            disk = asyncio.ensure_future(self.disk_ok.wait())
            await asyncio.wait({stop, disk}, return_when=asyncio.FIRST_COMPLETED)
            stop.cancel()
            disk.cancel()
        return not self.shutdown.stopping


class StopCrawl(Exception):
    """Raised inside a worker when it must stop (e.g. a raw write failed)."""


class MatchWorker:
    """Seeds players, fetches their match ids, then each new match (+ timeline)."""

    def __init__(self, ctx: CrawlContext, platform: str, seeder, rank_wakeup: asyncio.Event | None = None) -> None:
        self.ctx = ctx
        self.platform = platform.lower()
        self.platform_id = platform.upper()
        self.region = region_of(platform)
        self.seeder = seeder
        self.state = ctx.storage.state
        self.rank_wakeup = rank_wakeup or asyncio.Event()
        self.stats = ctx.stats.setdefault(self.platform_id, PlatformStats())
        self._seed_error_pause = SEED_ERROR_PAUSE

    async def run(self, *, seed: bool = True) -> None:
        """Process queued matches, then keep seeding until stopped (or seeding runs out)."""
        try:
            await self.drain_queue()
            if seed and await self._prepare_seeder():
                await self._seed_loop()
        except StopCrawl:
            pass
        finally:
            self.stats.finished = True
            self.rank_wakeup.set()

    async def _prepare_seeder(self) -> bool:
        """Run `seeder.prepare()`; False if it fails permanently (e.g. unknown SEED_RIOT_ID) or we stop."""
        while True:
            try:
                await self.seeder.prepare()
                return True
            except RiotApiError as exc:
                self._park("league", "prepare", exc)
                if isinstance(exc, PERMANENT_ERRORS):
                    log.error("%s: cannot start seeding: %s", self.platform_id, exc)
                    return False
                if not await self._pause_after_seed_error():
                    return False

    async def _seed_loop(self) -> None:
        while await self.ctx.ready() and not self.ctx.target_reached():
            try:
                next_seed = await self.seeder.next_seed()
            except RiotApiError as exc:
                self._park("league", f"seed:{self.stats.current_tier or '-'}", exc)
                await self._pause_after_seed_error()
                continue
            self._seed_error_pause = SEED_ERROR_PAUSE
            if next_seed is None:
                log.warning("%s: no more seed players - match worker done", self.platform_id)
                return
            await self.visit(next_seed)
            self.stats.seeds += 1
            if self.stats.seeds % RETRY_PARKED_EVERY_SEEDS == 0:
                self.retry_parked()
            await self.drain_queue()

    async def _pause_after_seed_error(self) -> bool:
        """Back off after a failed seeding call (doubling); False if the crawler is stopping."""
        pause = self._seed_error_pause
        self._seed_error_pause = min(pause * 2, SEED_ERROR_PAUSE_MAX)
        log.warning("%s: seeding paused for %.0f s", self.platform_id, pause)
        try:
            await asyncio.wait_for(self.ctx.shutdown.event.wait(), pause)
        except TimeoutError:
            return True
        return False

    async def visit(self, seed: Seed) -> None:
        """Fetch the next K match ids of a seed player and queue the unseen ones."""
        self.stats.current_tier = seed.tier
        k = self.ctx.settings.matches_per_player
        start_time, end_time = self.ctx.window()
        try:
            ids = await match.match_ids_by_puuid(
                self.ctx.client,
                self.region,
                seed.puuid,
                queue=self.ctx.settings.queue_id,
                start_time=start_time,
                end_time=end_time,
                start=seed.start,
                count=k,
            )
        except RiotApiError as exc:
            self._park("ids", seed.puuid, exc)
            self.state.players.mark_visited(self.platform_id, seed.puuid, next_start=seed.start, exhausted=True)
            return
        self.state.parked.remove("ids", self.platform_id, seed.puuid)
        new = self.state.matches.enqueue(
            self.platform_id, ids, seed_tier=seed.tier, seed_puuid=seed.puuid, strategy=self.seeder.strategy
        )
        self.state.players.mark_visited(
            self.platform_id, seed.puuid, next_start=seed.start + len(ids), exhausted=len(ids) < k
        )
        log.debug("%s %s: %d ids from offset %d, %d new", self.platform_id, seed.tier, len(ids), seed.start, len(new))

    async def drain_queue(self) -> None:
        """Fetch every queued match of this platform (also the ones left by a previous run)."""
        while await self.ctx.ready() and not self.ctx.target_reached():
            queued = self.state.matches.queued(self.platform_id, limit=20)
            if not queued:
                return
            for row in queued:
                if not await self.ctx.ready() or self.ctx.target_reached():
                    return
                await self.process(row)

    async def process(self, row: MatchRow) -> None:
        """Fetch + store the match and its timeline (whatever is missing), then finish it."""
        settings = self.ctx.settings
        data = timeline = None
        if not row.match_done:
            try:
                data = await match.match(self.ctx.client, self.region, row.match_id)
            except RiotApiError as exc:
                self._park("match", row.match_id, exc)
                return
            self._write(
                self.ctx.storage.persist_match,
                self.platform_id,
                row.match_id,
                data,
                seed_tier=row.seed_tier,
                seed_puuid=row.seed_puuid,
                strategy=row.strategy or self.seeder.strategy,
            )
        if settings.fetch_timelines and not row.timeline_done:
            try:
                timeline = await match.timeline(self.ctx.client, self.region, row.match_id)
            except RiotApiError as exc:
                self._park("timeline", row.match_id, exc)
                return
            self._write(self.ctx.storage.persist_timeline, self.platform_id, row.match_id, timeline)
            self.stats.timelines += 1
        if self.state.finish_match(row.match_id):
            self.stats.matches += 1
            if self.ctx.target_reached():
                self.ctx.shutdown.request_stop(f"--max-matches {self.ctx.max_matches} reached")
        source = data if data is not None else timeline
        if source is not None:
            self._after_match(source)

    def _after_match(self, data: dict) -> None:
        """Queue the participants (from a match or timeline) for rank lookups and as snowball seeds."""
        participants = [p for p in data.get("metadata", {}).get("participants", []) if isinstance(p, str) and p]
        hours = self.ctx.settings.rank_reuse_hours
        wanted = [p for p in participants if not self.state.rank_lookups.recent(self.platform_id, p, hours)]
        if self.state.rank_queue.add(self.platform_id, wanted):
            self.rank_wakeup.set()
        if self.seeder.strategy == "snowball":
            self.state.players.add(self.platform_id, participants, tier=None, source="snowball")

    def retry_parked(self, *, limit: int = RETRY_PARKED_BATCH, min_age: float = RETRY_PARKED_MIN_AGE) -> int:
        """Put a few retryable parked items of this platform back into their queues."""
        return requeue_parked(self.ctx.storage, self.platform_id, limit=limit, min_age=min_age)

    def _park(self, kind: str, key: str, exc: RiotApiError) -> None:
        level = logging.INFO if isinstance(exc, PERMANENT_ERRORS) else logging.WARNING
        log.log(level, "%s: parked %s %s: %s", self.platform_id, kind, key, exc)
        self.state.park(kind, self.platform_id, key, str(exc), exc.status)
        self.stats.parked += 1

    def _write(self, persist, *args, **kwargs) -> None:
        try:
            persist(*args, **kwargs)
        except OSError as exc:
            log.error("%s: writing raw data failed: %s", self.platform_id, exc)
            self.ctx.shutdown.request_stop(f"write error: {exc}")
            raise StopCrawl from exc


class RankWorker:
    """Looks up the current rank of queued participants (platform host, never blocks matches)."""

    def __init__(
        self,
        ctx: CrawlContext,
        platform: str,
        wakeup: asyncio.Event,
        *,
        match_worker_done=lambda: False,
        only: set[str] | None = None,
    ) -> None:
        self.ctx = ctx
        self.platform = platform.lower()
        self.platform_id = platform.upper()
        self.state = ctx.storage.state
        self.wakeup = wakeup
        self.match_worker_done = match_worker_done
        self.only = only
        self.stats = ctx.stats.setdefault(self.platform_id, PlatformStats())

    async def run(self) -> None:
        """Work through the rank queue (newest matches first) until stopped.

        Ends on its own when the match worker is done and the queue is empty.
        """
        try:
            while await self.ctx.ready():
                batch = self.state.rank_queue.next(self.platform_id, RANK_BATCH, newest_first=True)
                if self.only is not None:
                    batch = [p for p in batch if p in self.only]
                if not batch:
                    if self.match_worker_done():
                        return
                    self.wakeup.clear()
                    try:
                        await asyncio.wait_for(self.wakeup.wait(), RANK_IDLE_POLL)
                    except TimeoutError:
                        pass
                    continue
                for puuid in batch:
                    if not await self.ctx.ready():
                        return
                    await self.lookup(puuid)
        except StopCrawl:
            pass

    async def lookup(self, puuid: str) -> None:
        if self.state.rank_lookups.recent(self.platform_id, puuid, self.ctx.settings.rank_reuse_hours):
            self.state.rank_queue.remove(self.platform_id, puuid)
            return
        try:
            entries = await league.entries_by_puuid(self.ctx.client, self.platform, puuid)
        except RiotApiError as exc:
            level = logging.INFO if isinstance(exc, PERMANENT_ERRORS) else logging.WARNING
            log.log(level, "%s: parked rank %s: %s", self.platform_id, puuid, exc)
            self.state.park("rank", self.platform_id, puuid, str(exc), exc.status)
            self.stats.parked += 1
            return
        try:
            status = self.ctx.storage.persist_rank(self.platform_id, puuid, entries)
        except OSError as exc:
            log.error("%s: writing raw data failed: %s", self.platform_id, exc)
            self.ctx.shutdown.request_stop(f"write error: {exc}")
            raise StopCrawl from exc
        self.stats.ranks += 1
        self.stats.rank_status[status] += 1


def requeue_parked(
    storage: Storage,
    platform_id: str,
    *,
    limit: int | None = RETRY_PARKED_BATCH,
    min_age: float = RETRY_PARKED_MIN_AGE,
    max_attempts: int | None = RETRY_PARKED_MAX_ATTEMPTS,
) -> int:
    """Move retryable parked items back to their queues; returns how many.

    Permanent failures (400/403/404) are never retried. The parked row stays until the item
    succeeds (then it is removed) or fails again (then its attempt count goes up).
    """
    state = storage.state
    now = time.time()
    count = 0
    for item in state.parked.items(platform_id=platform_id, limit=10_000):
        if limit is not None and count >= limit:
            break
        if item.status_code in PERMANENT_STATUS:
            continue
        if max_attempts is not None and item.attempts >= max_attempts:
            continue
        if now - item.last_attempt < min_age:
            continue
        if item.kind in ("match", "timeline"):
            state.matches.requeue(item.key)
        elif item.kind == "rank":
            state.rank_queue.add(platform_id, [item.key])
        elif item.kind == "ids":
            state.players.reset_visit(platform_id, item.key)
        elif item.kind == "league":
            state.parked.remove("league", platform_id, item.key)  # seeding picks other pages anyway
        count += 1
    if count:
        log.info("%s: retrying %d parked item(s)", platform_id, count)
    return count
