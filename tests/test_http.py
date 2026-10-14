"""Tests for riot_client.http (MockTransport + virtual clock, no live API)."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable

import httpx
import pytest

from riot_client.errors import (
    BadRequest,
    Forbidden,
    InvalidResponse,
    NetworkError,
    NotFound,
    RateLimited,
    RiotApiError,
    ServerError,
)
from riot_client.http import STATUS_PATH, RiotHttpClient
from riot_client.keys import KeyProvider
from tests.virtual_time import now, run

OLD_KEY = "RGAPI-11111111-aaaa-bbbb-cccc-000000000001"
NEW_KEY = "RGAPI-22222222-aaaa-bbbb-cccc-000000000002"
PATH = "/lol/match/v5/matches/EUN1_1"
LIMIT_HEADERS = {
    "X-App-Rate-Limit": "20:1,100:120",
    "X-App-Rate-Limit-Count": "1:1,1:120",
    "X-Method-Rate-Limit": "2000:10",
    "X-Method-Rate-Limit-Count": "1:10",
}


class NoRandom:
    def random(self) -> float:
        return 0.0


@dataclass
class Env:
    """Stands in for the `.env` file: what `reload` returns."""

    key: str | None = OLD_KEY


@dataclass
class Recorder:
    """MockTransport handler: records requests, answers via `respond(request, n)`."""

    respond: Callable[[httpx.Request, int], httpx.Response]
    requests: list[httpx.Request] = field(default_factory=list)
    times: list[float] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.times.append(now())
        return self.respond(request, len(self.requests))

    def api_requests(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path != STATUS_PATH]


def ok(json=None, headers=None) -> httpx.Response:
    return httpx.Response(200, json=json if json is not None else {"ok": True}, headers={**LIMIT_HEADERS, **(headers or {})})


def status(code: int, headers=None, json=None) -> httpx.Response:
    return httpx.Response(code, json=json or {"status": {"message": "x", "status_code": code}}, headers=headers or {})


def make_client(handler: Recorder, env: Env | None = None, **options) -> RiotHttpClient:
    env = env or Env()
    keys = KeyProvider(env.key or "", lambda: env.key, poll_seconds=30, notify=False, stdin=_NotATty())
    defaults = dict(probe_host="eun1", max_retries=5, offline_after=300, rng=NoRandom())
    defaults.update(options)
    return RiotHttpClient(keys, transport=httpx.MockTransport(handler), **defaults)


class _NotATty:
    def isatty(self) -> bool:
        return False


def by_token(valid: str) -> Callable[[httpx.Request, int], httpx.Response]:
    """Answer 200 for `valid` key, 403 otherwise (for API and status endpoints alike)."""

    def respond(request: httpx.Request, _n: int) -> httpx.Response:
        if request.headers.get("X-Riot-Token") == valid:
            return ok()
        return status(403)

    return respond


# ---------------------------------------------------------------- basics


def test_success_sends_key_only_in_header():
    handler = Recorder(lambda r, n: ok({"metadata": {"matchId": "EUN1_1"}}))

    async def scenario():
        async with make_client(handler) as client:
            return await client.get_json("europe", PATH, {"start": 0, "count": 5}, method_key="match-v5.match")

    assert run(scenario()) == {"metadata": {"matchId": "EUN1_1"}}
    request = handler.requests[0]
    assert str(request.url) == "https://europe.api.riotgames.com/lol/match/v5/matches/EUN1_1?start=0&count=5"
    assert request.headers["X-Riot-Token"] == OLD_KEY
    assert "RGAPI" not in str(request.url)


def test_headers_update_limiter_and_stats():
    handler = Recorder(lambda r, n: ok())

    async def scenario():
        async with make_client(handler) as client:
            await client.get_json("europe", PATH, method_key="match-v5.match")
            return client.limiters.get("europe").describe(), client.stats["europe"]

    state, stats = run(scenario())
    assert state["methods"] == {"match-v5.match": [(1, 2000, 10)]}
    assert stats.requests == 1 and stats.statuses[200] == 1


@pytest.mark.parametrize(("code", "error"), [(400, BadRequest), (404, NotFound), (405, RiotApiError)])
def test_client_errors_are_not_retried(code, error):
    handler = Recorder(lambda r, n: status(code))

    async def scenario():
        async with make_client(handler) as client:
            await client.get_json("europe", PATH, method_key="m")

    with pytest.raises(error) as info:
        run(scenario())
    assert info.value.status == code
    assert len(handler.requests) == 1


def test_invalid_json_raises_invalid_response():
    handler = Recorder(lambda r, n: httpx.Response(200, text="<html>oops</html>"))

    async def scenario():
        async with make_client(handler) as client:
            await client.get_json("europe", PATH, method_key="m")

    with pytest.raises(InvalidResponse):
        run(scenario())


def test_key_in_params_or_path_is_refused():
    handler = Recorder(lambda r, n: ok())

    async def scenario(path, params):
        async with make_client(handler) as client:
            await client.get_json("eun1", path, params, method_key="m")

    with pytest.raises(ValueError):
        run(scenario(PATH, {"api_key": "x"}))
    with pytest.raises(ValueError):
        run(scenario(PATH, {"q": OLD_KEY}))
    with pytest.raises(ValueError):
        run(scenario(f"/x/{OLD_KEY}", None))
    assert handler.requests == []


# ---------------------------------------------------------------- 429


def test_429_with_retry_after_waits_exactly_that():
    handler = Recorder(
        lambda r, n: status(429, {"Retry-After": "7", "X-Rate-Limit-Type": "application"}) if n == 1 else ok()
    )

    async def scenario():
        async with make_client(handler) as client:
            return await client.get_json("europe", PATH, method_key="m")

    assert run(scenario()) == {"ok": True}
    assert handler.times == [0.0, pytest.approx(7.0)]


def test_429_method_scope_blocks_only_that_method():
    def respond(request, n):
        if request.url.path == PATH and n == 1:
            return status(429, {"Retry-After": "10", "X-Rate-Limit-Type": "method"})
        return ok()

    handler = Recorder(respond)

    async def both():
        async with make_client(handler) as client:
            first = asyncio.create_task(client.get_json("europe", PATH, method_key="a"))
            await asyncio.sleep(0.5)
            await client.get_json("europe", "/other", method_key="b")
            other_done = now()
            await first
            return other_done, now()

    other_done, a_done = run(both())
    assert other_done == pytest.approx(0.5)
    assert a_done == pytest.approx(10.0)


def test_429_service_without_retry_after_backs_off_then_raises():
    handler = Recorder(lambda r, n: status(429, {"X-Rate-Limit-Type": "service"}))

    async def scenario():
        async with make_client(handler, max_retries=4) as client:
            await client.get_json("europe", PATH, method_key="m")

    with pytest.raises(RateLimited):
        run(scenario())
    assert handler.times == [0.0, 1.0, 3.0, 7.0]


def test_429_service_backoff_then_success():
    handler = Recorder(lambda r, n: status(429, {"X-Rate-Limit-Type": "service"}) if n <= 2 else ok())

    async def scenario():
        async with make_client(handler) as client:
            return await client.get_json("europe", PATH, method_key="m")

    assert run(scenario()) == {"ok": True}
    assert handler.times == [0.0, 1.0, 3.0]


# ---------------------------------------------------------------- 5xx / network


def test_5xx_retried_then_raised_after_max_retries():
    handler = Recorder(lambda r, n: status(503))

    async def scenario():
        async with make_client(handler, max_retries=5) as client:
            await client.get_json("europe", PATH, method_key="m")

    with pytest.raises(ServerError) as info:
        run(scenario())
    assert info.value.attempts == 5
    assert handler.times == [0.0, 1.0, 3.0, 7.0, 15.0]


def test_5xx_then_success():
    handler = Recorder(lambda r, n: status(502) if n <= 2 else ok())

    async def scenario():
        async with make_client(handler) as client:
            return await client.get_json("europe", PATH, method_key="m")

    assert run(scenario()) == {"ok": True}


def test_backoff_is_capped():
    handler = Recorder(lambda r, n: status(500))

    async def scenario():
        async with make_client(handler, max_retries=9, offline_after=10_000) as client:
            await client.get_json("europe", PATH, method_key="m")

    with pytest.raises(ServerError):
        run(scenario())
    gaps = [b - a for a, b in zip(handler.times, handler.times[1:])]
    assert gaps == [1, 2, 4, 8, 16, 32, 60, 60]


def test_timeouts_retried_then_raised():
    def respond(request, n):
        raise httpx.ReadTimeout("timed out", request=request)

    handler = Recorder(respond)

    async def scenario():
        async with make_client(handler, max_retries=3) as client:
            await client.get_json("europe", PATH, method_key="m")

    with pytest.raises(NetworkError) as info:
        run(scenario())
    assert info.value.attempts == 3
    assert len(handler.requests) == 3


# ---------------------------------------------------------------- offline


def test_offline_mode_entered_and_left_without_consuming_retries():
    network_back_at = 100.0

    def respond(request, n):
        if now() < network_back_at:
            raise httpx.ConnectError("no route", request=request)
        return ok()

    handler = Recorder(respond)
    seen = {}

    async def scenario():
        async with make_client(handler, max_retries=4, offline_after=5) as client:
            task = asyncio.create_task(client.get_json("europe", PATH, method_key="m"))
            await asyncio.sleep(50)
            seen["paused"] = client.paused_reason
            result = await task
            seen["after"] = client.paused_reason
            return result

    assert run(scenario()) == {"ok": True}
    assert seen == {"paused": "offline", "after": None}
    # Failures at 0, 1, 3 consume 3 of 4 attempts; the one at 7 switches to offline instead of raising.
    api_times = [t for r, t in zip(handler.requests, handler.times) if r.url.path == PATH]
    assert api_times[:4] == [0.0, 1.0, 3.0, 7.0]
    probes = [t for r, t in zip(handler.requests, handler.times) if r.url.path == STATUS_PATH]
    assert probes == [37.0, 67.0, 97.0, 127.0]  # every 30 s until the network is back
    assert api_times[-1] == pytest.approx(127.0)


def test_service_429_does_not_count_as_offline():
    handler = Recorder(lambda r, n: status(429, {"X-Rate-Limit-Type": "service"}))

    async def scenario():
        async with make_client(handler, max_retries=3, offline_after=0.5) as client:
            try:
                await client.get_json("europe", PATH, method_key="m")
            except RateLimited:
                return client.paused_reason

    assert run(scenario()) is None


# ---------------------------------------------------------------- 401 / 403 key pause


def test_rejected_key_pauses_until_env_changes_then_retries():
    handler = Recorder(lambda r, n: by_token(NEW_KEY)(r, n))
    env = Env(OLD_KEY)
    seen = {}

    async def scenario():
        async with make_client(handler, env) as client:
            task = asyncio.create_task(client.get_json("europe", PATH, method_key="m"))
            await asyncio.sleep(10)
            seen["paused"] = client.paused_reason
            await asyncio.sleep(35)
            env.key = NEW_KEY  # user edits .env at t=45; next poll at t=60
            result = await task
            seen["done_at"] = now()
            seen["after"] = client.paused_reason
            seen["app"] = client.limiters.get("europe").describe()["app"]
            return result

    assert run(scenario()) == {"ok": True}
    assert seen["paused"] == "key"
    assert seen["after"] is None
    assert seen["done_at"] == pytest.approx(60.0)
    assert seen["app"][0][0] == 1  # limiter was reset; only the retried request counted
    api = handler.api_requests()
    assert [r.headers["X-Riot-Token"] for r in api] == [OLD_KEY, NEW_KEY]
    assert api[0].url == api[1].url  # the same request was retried


def test_enter_forces_immediate_reload():
    handler = Recorder(lambda r, n: by_token(NEW_KEY)(r, n))
    env = Env(OLD_KEY)

    async def scenario():
        async with make_client(handler, env) as client:
            task = asyncio.create_task(client.get_json("europe", PATH, method_key="m"))
            await asyncio.sleep(5)
            env.key = NEW_KEY
            await asyncio.sleep(1)
            client.keys.request_reload()  # what pressing Enter does
            await task
            return now()

    assert run(scenario()) == pytest.approx(6.0)


def test_new_key_also_invalid_keeps_waiting():
    third = "RGAPI-33333333-aaaa-bbbb-cccc-000000000003"
    handler = Recorder(lambda r, n: by_token(third)(r, n))
    env = Env(OLD_KEY)

    async def scenario():
        async with make_client(handler, env) as client:
            task = asyncio.create_task(client.get_json("europe", PATH, method_key="m"))
            await asyncio.sleep(1)
            env.key = NEW_KEY  # also rejected (poll at 30)
            await asyncio.sleep(40)
            assert client.paused_reason == "key"
            env.key = third  # accepted at the next poll (t=60)
            await task
            return now()

    assert run(scenario()) == pytest.approx(60.0)


def test_concurrent_rejections_share_one_recovery():
    handler = Recorder(lambda r, n: by_token(NEW_KEY)(r, n))
    env = Env(OLD_KEY)

    async def scenario():
        async with make_client(handler, env) as client:
            tasks = [asyncio.create_task(client.get_json("europe", f"/p/{i}", method_key="m")) for i in range(3)]
            await asyncio.sleep(1)
            env.key = NEW_KEY
            client.keys.request_reload()
            return await asyncio.gather(*tasks)

    assert run(scenario()) == [{"ok": True}] * 3
    validations = [r for r in handler.requests if r.url.path == STATUS_PATH]
    # One check of the old key and one of the new key - not one per failed request.
    assert [r.headers["X-Riot-Token"] for r in validations] == [OLD_KEY, NEW_KEY]


def test_403_with_valid_key_raises_forbidden_without_pause():
    def respond(request, n):
        return ok() if request.url.path == STATUS_PATH else status(403)

    handler = Recorder(respond)

    async def scenario():
        async with make_client(handler) as client:
            with pytest.raises(Forbidden):
                await client.get_json("europe", PATH, method_key="m")
            with pytest.raises(Forbidden):
                await client.get_json("europe", PATH, method_key="m")
            return now(), client.paused_reason

    assert run(scenario()) == (0.0, None)
    assert len([r for r in handler.requests if r.url.path == STATUS_PATH]) == 1  # cached validity


def test_unparseable_env_keeps_waiting():
    handler = Recorder(lambda r, n: by_token(NEW_KEY)(r, n))
    state = {"broken": True}

    def reload():
        if state["broken"]:
            raise ValueError("Invalid configuration: half-edited file")
        return NEW_KEY

    async def scenario():
        keys = KeyProvider(OLD_KEY, reload, poll_seconds=30, notify=False, stdin=_NotATty())
        client = RiotHttpClient(keys, probe_host="eun1", transport=httpx.MockTransport(handler), rng=NoRandom())
        async with client:
            task = asyncio.create_task(client.get_json("europe", PATH, method_key="m"))
            await asyncio.sleep(40)
            state["broken"] = False
            await task
            return now()

    assert run(scenario()) == pytest.approx(60.0)


# ---------------------------------------------------------------- secrets


def test_key_never_in_errors_or_logs(caplog):
    caplog.set_level(logging.DEBUG)
    responses = iter([status(503), status(404)])
    handler = Recorder(lambda r, n: next(responses))

    async def scenario():
        async with make_client(handler) as client:
            await client.get_json("europe", PATH, {"start": 0}, method_key="m")

    with pytest.raises(NotFound) as info:
        run(scenario())
    assert "RGAPI" not in str(info.value)
    assert "RGAPI" not in repr(info.value.url)
    for record in caplog.records:
        assert "RGAPI" not in record.getMessage()
    for request in handler.requests:
        assert "RGAPI" not in str(request.url)


def test_key_pause_logs_do_not_contain_key(caplog):
    caplog.set_level(logging.DEBUG)
    handler = Recorder(lambda r, n: by_token(NEW_KEY)(r, n))
    env = Env(OLD_KEY)

    async def scenario():
        async with make_client(handler, env) as client:
            task = asyncio.create_task(client.get_json("europe", PATH, method_key="m"))
            await asyncio.sleep(1)
            env.key = NEW_KEY
            await task

    run(scenario())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "PAUSED" in text
    assert OLD_KEY not in text and NEW_KEY not in text
