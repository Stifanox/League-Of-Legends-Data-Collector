"""Tests for riot_client.ratelimit (virtual clock)."""

from __future__ import annotations

import httpx
import pytest

from riot_client.ratelimit import HostLimiter, RateLimiterRegistry, RateWindow, parse_limits
from tests.virtual_time import now, run


async def times_of(limiter: HostLimiter, n: int, method: str = "m") -> list[float]:
    times = []
    for _ in range(n):
        await limiter.acquire(method)
        times.append(now())
    return times


def test_parse_limits():
    assert parse_limits("20:1,100:120") == [(20, 1), (100, 120)]
    assert parse_limits(" 500:10 , 30000:600 ") == [(500, 10), (30000, 600)]
    assert parse_limits("garbage,5:x,7:0,3:2") == [(3, 2)]
    assert parse_limits(None) == []
    assert parse_limits("") == []


def test_window_capacity_uses_safety():
    assert RateWindow(20, 1).capacity(0.9) == 18
    assert RateWindow(100, 120).capacity(0.9) == 90
    assert RateWindow(1, 1).capacity(0.5) == 1


def test_21st_request_in_one_second_waits():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        return await times_of(limiter, 21)

    times = run(scenario())
    assert times[:20] == [0.0] * 20
    assert times[20] == pytest.approx(1.0)


def test_101st_request_in_120_seconds_waits():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        return await times_of(limiter, 101)

    times = run(scenario())
    assert times[99] == pytest.approx(4.0)  # 5 bursts of 20 per second
    assert times[100] == pytest.approx(120.0)


def test_safety_factor_lowers_effective_limit():
    async def scenario():
        limiter = HostLimiter("eun1", safety=0.9)
        return await times_of(limiter, 19)

    times = run(scenario())
    assert times[17] == 0.0
    assert times[18] == pytest.approx(1.0)


def test_count_resync_takes_server_maximum():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        await limiter.acquire("m")
        limiter.update_from_headers(
            "m", httpx.Headers({"X-App-Rate-Limit": "20:1,100:120", "X-App-Rate-Limit-Count": "20:1,60:120"})
        )
        app = limiter.describe()["app"]
        await limiter.acquire("m")  # 1 s window is full according to the server
        return app, now()

    app, waited_until = run(scenario())
    assert app == [(20, 20, 1), (60, 100, 120)]
    assert waited_until == pytest.approx(1.0)


def test_lower_server_count_does_not_reduce_local_count():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        for _ in range(5):
            await limiter.acquire("m")
        limiter.update_from_headers("m", httpx.Headers({"X-App-Rate-Limit-Count": "2:1,2:120"}))
        return limiter.describe()["app"]

    assert run(scenario()) == [(5, 20, 1), (5, 100, 120)]


def test_new_limits_from_headers_replace_defaults():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        limiter.update_from_headers("m", httpx.Headers({"X-App-Rate-Limit": "500:10,30000:600"}))
        return limiter.describe()["app"], await times_of(limiter, 25)

    app, times = run(scenario())
    assert app == [(0, 500, 10), (0, 30000, 600)]
    assert times[-1] == 0.0  # 20/1 s default no longer applies


def test_method_window_is_independent_of_app_window():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        await limiter.acquire("slow")
        limiter.update_from_headers(
            "slow", httpx.Headers({"X-Method-Rate-Limit": "2:10", "X-Method-Rate-Limit-Count": "1:10"})
        )
        slow = await times_of(limiter, 2, "slow")
        fast = await times_of(limiter, 3, "fast")
        return slow, fast

    slow, fast = run(scenario())
    assert slow == [0.0, pytest.approx(10.0)]
    assert fast == [pytest.approx(10.0)] * 3  # only limited by the app window, not "slow"


def test_other_method_not_delayed_by_method_limit():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        limiter.update_from_headers(
            "slow", httpx.Headers({"X-Method-Rate-Limit": "1:10", "X-Method-Rate-Limit-Count": "1:10"})
        )
        return await times_of(limiter, 3, "fast")

    assert run(scenario()) == [0.0, 0.0, 0.0]


def test_hosts_are_independent():
    async def scenario():
        registry = RateLimiterRegistry(safety=1.0)
        eun1 = await times_of(registry.get("eun1"), 20)
        europe = await times_of(registry.get("europe"), 20)
        return eun1, europe

    eun1, europe = run(scenario())
    assert eun1[-1] == 0.0 and europe[-1] == 0.0


def test_block_application_and_method():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        limiter.block(7, "method", "a")
        b = await times_of(limiter, 1, "b")
        a = await times_of(limiter, 1, "a")
        limiter.block(5, "application", "a")
        b2 = await times_of(limiter, 1, "b")
        return b, a, b2

    b, a, b2 = run(scenario())
    assert b == [0.0]
    assert a == [pytest.approx(7.0)]
    assert b2 == [pytest.approx(12.0)]


def test_reset_clears_counts_and_blocks():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        await times_of(limiter, 20)
        limiter.block(60, "application", "m")
        limiter.reset()
        return await times_of(limiter, 1)

    assert run(scenario()) == [0.0]


def test_recent_rate():
    async def scenario():
        limiter = HostLimiter("eun1", safety=1.0)
        await times_of(limiter, 10)
        return limiter.recent_rate(span=10)

    assert run(scenario()) == pytest.approx(1.0)
