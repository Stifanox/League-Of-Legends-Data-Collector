"""Runs the crawler: one match worker and one rank worker per platform (DESIGN §8, §12)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from crawler.config import CRAWL_MODES, Settings
from crawler.progress import Progress, tier_lines
from crawler.riot import build_client
from crawler.seeding import SnowballSeeder, StratifiedSeeder
from crawler.shutdown import DiskGuard, ShutdownController
from crawler.storage import Storage
from crawler.workers import CrawlContext, MatchWorker, RankWorker, requeue_parked

log = logging.getLogger(__name__)

DISK_CHECK_SECONDS = 120.0
# After a stop request, in-flight work gets this long before tasks are cancelled.
SHUTDOWN_GRACE_SECONDS = 15.0

_DURATION = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """`"48h"` -> 172800.0; units s/m/h/d, plain numbers are seconds."""
    match = _DURATION.match(text)
    if match is None:
        raise ValueError(f"Invalid duration {text!r}; use e.g. 90m, 48h, 2d")
    return float(match.group(1)) * _UNIT_SECONDS[match.group(2).lower()]


def warn_about_split_reset(settings: Settings) -> None:
    if settings.split_reset_date is None:
        return
    window_start = (datetime.now(UTC) - timedelta(days=settings.window_days)).date()
    if window_start < settings.split_reset_date:
        log.warning(
            "The %d-day window starts %s, before SPLIT_RESET_DATE %s: early matches come from the previous split",
            settings.window_days,
            window_start,
            settings.split_reset_date,
        )


async def crawl(
    settings: Settings,
    env_file: Path,
    *,
    max_matches: int | None = None,
    duration: float | None = None,
    platforms: list[str] | None = None,
    mode: str | None = None,
    show_progress: bool = True,
    install_signals: bool = True,
    rng: random.Random | None = None,
    **client_options: Any,
) -> CrawlContext:
    """Crawl until stopped (signal, `max_matches`, `duration`) or there is no work left.

    `mode` (default `settings.crawl_mode`):
    - `full`: match and rank workers run in parallel;
    - `matches-only`: no rank lookups, participants are only queued for a later `ranks-only` run;
    - `ranks-only`: no new players or matches, the saved rank queue is worked off.
    """
    platforms = [p.lower() for p in (platforms or settings.regions)]
    mode = mode or settings.crawl_mode
    if mode not in CRAWL_MODES:
        raise ValueError(f"mode must be one of {', '.join(CRAWL_MODES)}, got {mode!r}")
    if mode != "ranks-only":
        warn_about_split_reset(settings)
    elif max_matches is not None:
        log.warning("--max-matches has no effect in ranks-only mode")
    log.info(
        "Crawling %s | mode %s | strategy %s | queue %d | last %d days | K=%d | timelines %s",
        ",".join(platforms),
        mode,
        settings.strategy,
        settings.queue_id,
        settings.window_days,
        settings.matches_per_player,
        "on" if settings.fetch_timelines else "off",
    )
    rng = rng or random.Random()

    def make_workers(ctx: CrawlContext) -> list[Awaitable[None]]:
        jobs = []
        for platform in platforms:
            if settings.strategy == "snowball":
                seeder = SnowballSeeder(ctx.client, ctx.storage.state, platform, settings.seed_riot_id or "")
            else:
                seeder = StratifiedSeeder(ctx.client, ctx.storage.state, platform, rng=rng)
            wakeup = asyncio.Event()
            matches = MatchWorker(ctx, platform, seeder, wakeup)
            if mode == "ranks-only":
                jobs.append(RankWorker(ctx, platform, wakeup, match_worker_done=lambda: True).run())
                continue
            jobs.append(matches.run())
            if mode == "full":
                jobs.append(RankWorker(ctx, platform, wakeup, match_worker_done=lambda s=matches.stats: s.finished).run())
        return jobs

    return await _run(
        settings,
        env_file,
        platforms,
        make_workers,
        progress_counts="ranks" if mode == "ranks-only" else "matches",
        max_matches=max_matches if mode != "ranks-only" else None,
        duration=duration,
        show_progress=show_progress,
        install_signals=install_signals,
        client_options=client_options,
    )


async def retry_parked(
    settings: Settings,
    env_file: Path,
    *,
    show_progress: bool = True,
    install_signals: bool = True,
    **client_options: Any,
) -> CrawlContext:
    """Retry every retryable parked item once (no seeding), then stop."""
    with Storage(settings.data_dir, roll_mb=settings.raw_roll_mb) as storage:
        platforms = [p.lower() for p in storage.state.platforms()]
        rank_keys = {
            (item.platform_id, item.key) for item in storage.state.parked.items(kind="rank", limit=1_000_000)
        }
        total = sum(
            requeue_parked(storage, p.upper(), limit=None, min_age=0, max_attempts=None) for p in platforms
        )
    log.info("retry-parked: %d item(s) requeued", total)

    def make_workers(ctx: CrawlContext) -> list[Awaitable[None]]:
        jobs = []
        for platform in platforms:
            seeder = StratifiedSeeder(ctx.client, ctx.storage.state, platform)  # unused when seed=False
            wakeup = asyncio.Event()
            matches = MatchWorker(ctx, platform, seeder, wakeup)
            only = {key for pid, key in rank_keys if pid == platform.upper()}
            ranks = RankWorker(ctx, platform, wakeup, match_worker_done=lambda s=matches.stats: s.finished, only=only)
            jobs += [matches.run(seed=False), ranks.run()]
        return jobs

    return await _run(
        settings,
        env_file,
        platforms or list(settings.regions),
        make_workers,
        show_progress=show_progress,
        install_signals=install_signals,
        client_options=client_options,
    )


async def _run(
    settings: Settings,
    env_file: Path,
    platforms: list[str],
    make_workers,
    *,
    max_matches: int | None = None,
    duration: float | None = None,
    progress_counts: str = "matches",
    show_progress: bool,
    install_signals: bool,
    client_options: dict[str, Any],
) -> CrawlContext:
    shutdown = ShutdownController()
    if install_signals:
        shutdown.install()
    try:
        with Storage(settings.data_dir, roll_mb=settings.raw_roll_mb) as storage:
            client_options.setdefault("probe_host", platforms[0])
            async with build_client(settings, env_file, **client_options) as client:
                ctx = CrawlContext(settings, client, storage, shutdown, max_matches=max_matches)
                disk = DiskGuard(settings.data_dir, settings.min_free_disk_gb)
                _apply_disk_state(ctx, disk)
                helpers = [asyncio.create_task(_watch_disk(ctx, disk))]
                if duration is not None:
                    helpers.append(asyncio.create_task(_stop_after(shutdown, duration)))
                if show_progress:
                    progress = Progress(ctx, client, platforms, disk_low=lambda: disk.low, counts=progress_counts)
                    helpers.append(asyncio.create_task(progress.run()))
                try:
                    await _run_workers(make_workers(ctx), shutdown)
                finally:
                    await _cancel(helpers)
                log.info(
                    "Crawl finished%s: %d new matches, %d rank lookups",
                    f" ({shutdown.reason})" if shutdown.reason else "",
                    ctx.total_matches,
                    ctx.total_ranks,
                )
                for line in tier_lines(storage.state, platforms):
                    log.info("%s", line)
                return ctx
    finally:
        if install_signals:
            shutdown.uninstall()


async def _run_workers(jobs: list[Awaitable[None]], shutdown: ShutdownController) -> None:
    """Run workers until all finish, or until a stop request (+ grace period, then cancel)."""
    workers = [asyncio.create_task(job) for job in jobs]
    for task in workers:
        task.add_done_callback(_log_failure)
    all_done = asyncio.ensure_future(asyncio.wait(workers))
    stop = asyncio.ensure_future(shutdown.event.wait())
    try:
        await asyncio.wait({all_done, stop}, return_when=asyncio.FIRST_COMPLETED)
        if not all_done.done():
            _, pending = await asyncio.wait(workers, timeout=SHUTDOWN_GRACE_SECONDS)
            if pending:
                log.info("Cancelling %d worker(s) still busy after %.0f s", len(pending), SHUTDOWN_GRACE_SECONDS)
            await _cancel(pending)
    finally:
        await _cancel([*workers, all_done, stop])


async def _cancel(tasks) -> None:
    tasks = [t for t in tasks if not t.done()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _log_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception() is not None:
        log.error("Worker crashed", exc_info=task.exception())


def _apply_disk_state(ctx: CrawlContext, disk: DiskGuard) -> None:
    if disk.check():
        ctx.disk_ok.set()
    else:
        ctx.disk_ok.clear()


async def _watch_disk(ctx: CrawlContext, disk: DiskGuard) -> None:
    while True:
        await asyncio.sleep(DISK_CHECK_SECONDS)
        _apply_disk_state(ctx, disk)


async def _stop_after(shutdown: ShutdownController, seconds: float) -> None:
    try:
        await asyncio.wait_for(shutdown.event.wait(), seconds)
    except TimeoutError:
        shutdown.request_stop(f"--duration {seconds / 3600:g}h reached")
