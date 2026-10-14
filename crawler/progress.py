"""Progress bar + compact status line (DESIGN §7). Falls back to log lines without a TTY."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import nullcontext
from datetime import UTC, datetime

from alive_progress import alive_bar

from crawler.state import StateStore
from crawler.workers import CrawlContext
from riot_client.http import RiotHttpClient
from riot_client.routing import region_of

log = logging.getLogger(__name__)

REFRESH_SECONDS = 1.0
LOG_EVERY_SECONDS = 60.0
TIERS_LOG_EVERY_SECONDS = 300.0
RATE_SPAN = 60.0

_TIER_SHORT = {
    "IRON": "I", "BRONZE": "B", "SILVER": "S", "GOLD": "G", "PLATINUM": "P", "EMERALD": "E",
    "DIAMOND": "D", "MASTER": "M", "GRANDMASTER": "GM", "CHALLENGER": "C",
}


class Progress:
    """Shows matches collected and, per platform, request rates, rank backlog and state flags."""

    def __init__(
        self,
        ctx: CrawlContext,
        client: RiotHttpClient,
        platforms: list[str],
        disk_low=lambda: False,
        counts: str = "matches",
    ) -> None:
        """`counts`: what the bar counts, `"matches"` or `"ranks"` (ranks-only mode)."""
        self.ctx = ctx
        self.counts = counts
        self.client = client
        self.platforms = [p.lower() for p in platforms]
        self.disk_low = disk_low
        self._last_pause: str | None = None

    async def run(self, *, tty: bool | None = None) -> None:
        tty = sys.stdout.isatty() if tty is None else tty
        bar_cm = alive_bar(self.ctx.max_matches, title=self.counts, dual_line=True) if tty else nullcontext()
        loop = asyncio.get_running_loop()
        next_log = next_tiers = loop.time()
        shown = 0
        with bar_cm as bar:
            while True:
                self._record_pause_changes()
                now = loop.time()
                if bar is not None:
                    total = self._count()
                    for _ in range(total - shown):
                        bar()
                    shown = total
                    bar.text = self.status_line()
                elif now >= next_log:
                    log.info("progress: %d %s | %s", self._count(), self.counts, self.status_line())
                    next_log = now + LOG_EVERY_SECONDS
                if now >= next_tiers:
                    for line in tier_lines(self.ctx.storage.state, self.platforms):
                        log.debug("%s", line) if bar is not None else log.info("%s", line)
                    next_tiers = now + TIERS_LOG_EVERY_SECONDS
                await asyncio.sleep(REFRESH_SECONDS)

    def _count(self) -> int:
        return self.ctx.total_ranks if self.counts == "ranks" else self.ctx.total_matches

    def status_line(self) -> str:
        """e.g. `EUN1 m:120 GOLD rq:340 0.81|0.79 r/s · KR m:98 ... · PAUSED: key`."""
        state = self.ctx.storage.state
        parts = []
        for platform in self.platforms:
            pid = platform.upper()
            stats = self.ctx.stats.get(pid)
            matches = stats.matches if stats else 0
            ranks = stats.ranks if stats else 0
            tier = (stats.current_tier or "-") if stats else "-"
            done = " done" if stats and stats.finished else ""
            regional = self.client.limiters.get(region_of(platform)).recent_rate(RATE_SPAN)
            platform_rate = self.client.limiters.get(platform).recent_rate(RATE_SPAN)
            parts.append(
                f"{pid} m:{matches} {tier}{done} r:{ranks} rq:{state.rank_queue.count(pid)} "
                f"{regional:.2f}|{platform_rate:.2f} r/s"
            )
        flags = []
        if self.client.paused_reason == "key":
            flags.append("PAUSED: key")
        elif self.client.paused_reason == "offline":
            flags.append("OFFLINE")
        if self.disk_low():
            flags.append("LOW DISK")
        return " · ".join(parts + flags)

    def _record_pause_changes(self) -> None:
        reason = self.client.paused_reason
        if reason == "key" and self._last_pause != "key":
            self.ctx.storage.state.meta.set("last_key_error", datetime.now(UTC).isoformat(timespec="seconds"))
        self._last_pause = reason


def tier_lines(state: StateStore, platforms: list[str]) -> list[str]:
    """Per-platform tier progress, e.g. `EUN1 tiers: I 10 B 12 ... C 3(x)` ((x) = exhausted)."""
    lines = []
    for platform in platforms:
        pid = platform.upper()
        tiers = state.tiers.progress(pid)
        if tiers:
            text = " ".join(f"{_TIER_SHORT.get(t.tier, t.tier)} {t.collected}{'(x)' if t.exhausted else ''}" for t in tiers)
            lines.append(f"{pid} tiers: {text}")
    return lines
