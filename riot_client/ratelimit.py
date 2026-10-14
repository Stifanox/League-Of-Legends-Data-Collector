"""Client-side rate limiting driven by Riot's rate limit headers (DESIGN §6).

Riot reports limits and current counts (`X-App-Rate-Limit`, `X-App-Rate-Limit-Count`,
`X-Method-Rate-Limit`, `X-Method-Rate-Limit-Count`, format `"20:1,100:120"`) but no reset
time, so each window tracks its own start locally and re-syncs its count from the headers.
Time comes from the running event loop, which lets tests use a virtual clock.

Riot's window starts when *its* server sees the first request, a little after our clock
does. To avoid sending into the tail of Riot's previous window, each local window is padded
by a small margin, and a high count reported right after our window rolled over is treated
as a leftover from Riot's previous window instead of filling the new one (that mistake
used to waste every other window).
"""

from __future__ import annotations

import asyncio
import math
from collections import deque
from collections.abc import Mapping

DEFAULT_APP_LIMITS = "20:1,100:120"

# Tolerance for float rounding when comparing window boundaries.
_EPSILON = 1e-6

# Local windows last `seconds + margin`, margin = min(MAX, RATIO * seconds): 1 s for 10 s+ windows.
WINDOW_MARGIN_MAX = 1.0
WINDOW_MARGIN_RATIO = 0.1
# Counts arriving this soon after our window rolled over may still describe Riot's old window.
STALE_COUNT_GRACE_MAX = 2.0
STALE_COUNT_GRACE_RATIO = 0.25


def parse_limits(value: str | None) -> list[tuple[int, int]]:
    """Parse `"20:1,100:120"` into `[(20, 1), (100, 120)]`; malformed parts are skipped."""
    pairs: list[tuple[int, int]] = []
    if not value:
        return pairs
    for part in value.split(","):
        amount, sep, seconds = part.strip().partition(":")
        try:
            pair = (int(amount), int(seconds))
        except ValueError:
            continue
        if sep and pair[0] >= 0 and pair[1] > 0:
            pairs.append(pair)
    return pairs


def _now() -> float:
    return asyncio.get_running_loop().time()


class RateWindow:
    """One fixed window (`limit` requests per `seconds`), started by the first request in it."""

    def __init__(self, limit: int, seconds: int) -> None:
        self.limit = limit
        self.seconds = seconds
        self.margin = min(WINDOW_MARGIN_MAX, WINDOW_MARGIN_RATIO * seconds)
        self.stale_grace = min(STALE_COUNT_GRACE_MAX, STALE_COUNT_GRACE_RATIO * seconds)
        self.start: float | None = None
        self.count = 0
        self.previous_end: float | None = None

    @property
    def end(self) -> float | None:
        return None if self.start is None else self.start + self.seconds + self.margin

    def _roll(self, now: float) -> None:
        if self.start is not None and now >= self.end - _EPSILON:
            self.previous_end = self.end
            self.start = None
            self.count = 0

    def capacity(self, safety: float) -> int:
        return max(1, math.floor(self.limit * safety + _EPSILON))

    def wait_time(self, now: float, safety: float) -> float:
        """Seconds until one more request fits (0 if it fits now)."""
        self._roll(now)
        if self.count < self.capacity(safety) or self.start is None:
            return 0.0
        return max(0.0, self.end - now)

    def record(self, now: float) -> None:
        self._roll(now)
        if self.start is None:
            self.start = now
        self.count += 1

    def sync(self, server_count: int, now: float) -> None:
        """Take the server's count if it is higher than ours.

        Ignored shortly after our window rolled over: then a high count most likely belongs to
        Riot's previous window, which ends a moment after ours. If Riot's window really is full
        (e.g. the key is shared), the next request gets a 429 with `Retry-After`.
        """
        self._roll(now)
        if server_count <= self.count:
            return
        if self.previous_end is not None and now - self.previous_end < self.stale_grace:
            return
        self.count = server_count
        if self.start is None:
            self.start = now

    def reset(self) -> None:
        self.start = None
        self.count = 0
        self.previous_end = None


class WindowSet:
    """All windows of one scope (the app, or one method) on one host."""

    def __init__(self, limits: list[tuple[int, int]]) -> None:
        self.windows = [RateWindow(limit, seconds) for limit, seconds in limits]

    def set_limits(self, limits: list[tuple[int, int]]) -> None:
        """Replace the limits, keeping the state of windows whose length did not change."""
        current = {w.seconds: w for w in self.windows}
        windows = []
        for limit, seconds in limits:
            window = current.get(seconds) or RateWindow(limit, seconds)
            window.limit = limit
            windows.append(window)
        self.windows = windows

    def sync(self, counts: list[tuple[int, int]], now: float) -> None:
        by_seconds = {w.seconds: w for w in self.windows}
        for count, seconds in counts:
            window = by_seconds.get(seconds)
            if window is not None:
                window.sync(count, now)

    def wait_time(self, now: float, safety: float) -> float:
        return max((w.wait_time(now, safety) for w in self.windows), default=0.0)

    def record(self, now: float) -> None:
        for window in self.windows:
            window.record(now)

    def reset(self) -> None:
        for window in self.windows:
            window.reset()

    def describe(self, now: float) -> list[tuple[int, int, int]]:
        """`(count, limit, seconds)` per window."""
        for window in self.windows:
            window._roll(now)
        return [(w.count, w.limit, w.seconds) for w in self.windows]


class HostLimiter:
    """Rate limiter for one routing host: app windows plus one window set per method."""

    def __init__(self, host: str, *, safety: float = 0.9, default_app_limits: str = DEFAULT_APP_LIMITS) -> None:
        self.host = host
        self.safety = safety
        self.app = WindowSet(parse_limits(default_app_limits))
        self.methods: dict[str, WindowSet] = {}
        self.blocked_until = 0.0
        self.method_blocked_until: dict[str, float] = {}
        self._sent: deque[float] = deque(maxlen=2000)

    def wait_time(self, method_key: str, now: float) -> float:
        waits = [
            self.blocked_until - now,
            self.method_blocked_until.get(method_key, 0.0) - now,
            self.app.wait_time(now, self.safety),
        ]
        method = self.methods.get(method_key)
        if method is not None:
            waits.append(method.wait_time(now, self.safety))
        return max(0.0, *waits)

    async def acquire(self, method_key: str) -> None:
        """Wait until every applicable window has room, then count the request."""
        while True:
            now = _now()
            wait = self.wait_time(method_key, now)
            if wait <= 0:
                self.app.record(now)
                method = self.methods.get(method_key)
                if method is not None:
                    method.record(now)
                self._sent.append(now)
                return
            await asyncio.sleep(wait)

    def update_from_headers(self, method_key: str, headers: Mapping[str, str]) -> None:
        """Learn limits and re-sync counts from a response's headers."""
        now = _now()
        app_limits = parse_limits(headers.get("X-App-Rate-Limit"))
        if app_limits:
            self.app.set_limits(app_limits)
        self.app.sync(parse_limits(headers.get("X-App-Rate-Limit-Count")), now)

        method_limits = parse_limits(headers.get("X-Method-Rate-Limit"))
        if method_limits:
            method = self.methods.get(method_key)
            if method is None:
                method = self.methods[method_key] = WindowSet(method_limits)
                # This request itself was sent before the method's limits were known.
                method.record(now)
            else:
                method.set_limits(method_limits)
        method = self.methods.get(method_key)
        if method is not None:
            method.sync(parse_limits(headers.get("X-Method-Rate-Limit-Count")), now)

    def block(self, seconds: float, scope: str, method_key: str) -> None:
        """Stop sending for `seconds`: the whole host (`application`) or one method."""
        until = _now() + seconds
        if scope == "application":
            self.blocked_until = max(self.blocked_until, until)
        else:
            self.method_blocked_until[method_key] = max(self.method_blocked_until.get(method_key, 0.0), until)

    def reset(self) -> None:
        """Forget all counts and blocks (used after a key change); learned limits are kept."""
        self.app.reset()
        for method in self.methods.values():
            method.reset()
        self.blocked_until = 0.0
        self.method_blocked_until.clear()

    def recent_rate(self, span: float = 10.0) -> float:
        """Requests per second sent over the last `span` seconds."""
        now = _now()
        return sum(1 for t in self._sent if now - t <= span) / span

    def describe(self) -> dict[str, object]:
        """Current counts and limits, for status output."""
        now = _now()
        return {
            "app": self.app.describe(now),
            "methods": {key: ws.describe(now) for key, ws in sorted(self.methods.items())},
        }


class RateLimiterRegistry:
    """One `HostLimiter` per routing host (e.g. `eun1`, `europe`)."""

    def __init__(self, *, safety: float = 0.9, default_app_limits: str = DEFAULT_APP_LIMITS) -> None:
        self.safety = safety
        self.default_app_limits = default_app_limits
        self._limiters: dict[str, HostLimiter] = {}

    def get(self, host: str) -> HostLimiter:
        limiter = self._limiters.get(host)
        if limiter is None:
            limiter = self._limiters[host] = HostLimiter(
                host, safety=self.safety, default_app_limits=self.default_app_limits
            )
        return limiter

    def reset_all(self) -> None:
        for limiter in self._limiters.values():
            limiter.reset()

    def items(self) -> list[tuple[str, HostLimiter]]:
        return sorted(self._limiters.items())
