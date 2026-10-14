"""Async HTTP client for the Riot API: rate limiting, retries, key pause, offline mode (DESIGN §6, §7)."""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

import httpx

from riot_client.errors import (
    BadRequest,
    Forbidden,
    InvalidResponse,
    NetworkError,
    NotFound,
    RateLimited,
    RiotApiError,
    ServerError,
    redact,
)
from riot_client.keys import KeyProvider
from riot_client.ratelimit import DEFAULT_APP_LIMITS, RateLimiterRegistry

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://{host}.api.riotgames.com"
STATUS_PATH = "/lol/status/v4/platform-data"
STATUS_METHOD_KEY = "lol-status.platform-data"
DEFAULT_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# A 403 right after the key was confirmed valid is treated as "endpoint forbidden".
_KEY_VALID_CACHE_SECONDS = 60.0


@dataclass
class HostStats:
    """Request counters for one routing host (for the status UI)."""

    requests: int = 0
    statuses: Counter = field(default_factory=Counter)
    network_errors: int = 0


class RiotHttpClient:
    """Sends GET requests to Riot routing hosts and returns parsed JSON.

    The key goes only in the `X-Riot-Token` header. Errors follow DESIGN §7:
    400 -> `BadRequest`, 404 -> `NotFound`, bad JSON -> `InvalidResponse`, 5xx/network are
    retried with backoff and raised after `max_retries` attempts, 429 waits as instructed,
    401/403 pause every request until a new valid key is found.
    """

    def __init__(
        self,
        keys: KeyProvider,
        *,
        probe_host: str,
        safety: float = 0.9,
        max_retries: int = 5,
        offline_after: float = 300.0,
        offline_probe_seconds: float = 30.0,
        backoff_base: float = 1.0,
        backoff_cap: float = 60.0,
        default_app_limits: str = DEFAULT_APP_LIMITS,
        base_url: str = DEFAULT_BASE_URL,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
        rng: random.Random | None = None,
        validate_key: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self.keys = keys
        self.probe_host = probe_host
        self.max_retries = max_retries
        self.offline_after = offline_after
        self.offline_probe_seconds = offline_probe_seconds
        self.backoff_base = backoff_base
        self.backoff_cap = backoff_cap
        self.base_url = base_url
        self.limiters = RateLimiterRegistry(safety=safety, default_app_limits=default_app_limits)
        self.stats: dict[str, HostStats] = {}
        self._rng = rng or random.Random()
        self._validate_key = validate_key or self._check_key
        self._http = httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"Accept": "application/json"},
        )
        # Set = requests may run. Cleared while the key is rejected / the network is down.
        self._resume = asyncio.Event()
        self._resume.set()
        self._online = asyncio.Event()
        self._online.set()
        self._key_task: asyncio.Task[bool] | None = None
        self._key_paused = False
        self._key_valid_at: tuple[str, float] | None = None
        self._probe_task: asyncio.Task[None] | None = None
        self._failing_since: float | None = None

    # ----------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> RiotHttpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        for task in (self._key_task, self._probe_task):
            if task is not None and not task.done():
                task.cancel()
        await self._http.aclose()

    # ----------------------------------------------------------------- state

    @property
    def paused_reason(self) -> str | None:
        """`"key"` while waiting for a new key, `"offline"` while the network is down, else `None`."""
        if self._key_paused:
            return "key"
        if not self._online.is_set():
            return "offline"
        return None

    def url(self, host: str, path: str) -> str:
        return self.base_url.format(host=host) + path

    # ----------------------------------------------------------------- requests

    async def get_json(
        self,
        host: str,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        method_key: str,
    ) -> Any:
        """GET `path` on routing `host` and return the parsed JSON body."""
        _reject_secrets(path, params)
        url = self.url(host, path)
        limiter = self.limiters.get(host)
        attempts = 0  # failed attempts that count towards max_retries
        while True:
            await self._wait_until_ready()
            await limiter.acquire(method_key)
            key = self.keys.key
            try:
                response = await self._send(host, url, params, key)
            except NetworkError as exc:
                exc.method_key = method_key
                attempts = await self._after_failure(exc, attempts)
                continue

            limiter.update_from_headers(method_key, response.headers)
            status = response.status_code
            if status < 500:
                self._mark_reachable()

            if status == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise InvalidResponse(
                        f"Response is not valid JSON: {exc}", status=status, url=url, method_key=method_key
                    ) from None
            if status in (401, 403):
                if await self._handle_rejected_key(key):
                    continue
                raise Forbidden("Forbidden although the key is valid", status=status, url=url, method_key=method_key)
            if status == 429:
                attempts = await self._handle_rate_limited(response, limiter, method_key, url, attempts)
                continue
            if status == 400:
                raise BadRequest(_body_message(response), status=status, url=url, method_key=method_key)
            if status == 404:
                raise NotFound(_body_message(response), status=status, url=url, method_key=method_key)
            if status >= 500:
                error = ServerError(_body_message(response), status=status, url=url, method_key=method_key)
                attempts = await self._after_failure(error, attempts)
                continue
            raise RiotApiError(_body_message(response), status=status, url=url, method_key=method_key)

    async def _send(self, host: str, url: str, params: Mapping[str, Any] | None, key: str) -> httpx.Response:
        stats = self.stats.setdefault(host, HostStats())
        stats.requests += 1
        try:
            response = await self._http.get(url, params=params, headers={"X-Riot-Token": key})
        except httpx.TransportError as exc:
            stats.network_errors += 1
            log.debug("GET %s failed: %s", url, type(exc).__name__)
            raise NetworkError(f"{type(exc).__name__}: {exc}", url=url) from None
        stats.statuses[response.status_code] += 1
        log.debug("GET %s -> %d", url, response.status_code)
        return response

    async def _wait_until_ready(self) -> None:
        while True:
            if not self._resume.is_set():
                await self._resume.wait()
            elif not self._online.is_set():
                await self._online.wait()
            else:
                return

    # ----------------------------------------------------------------- 5xx / network / offline

    def _backoff(self, attempt: int) -> float:
        delay = min(self.backoff_cap, self.backoff_base * 2 ** (attempt - 1))
        return min(self.backoff_cap, delay * (1 + 0.25 * self._rng.random()))

    async def _after_failure(self, error: RiotApiError, attempts: int) -> int:
        """Count a 5xx/network failure, back off, and return the new attempt count.

        Raises `error` once `max_retries` attempts failed. While offline (or when this
        failure switches to offline mode) attempts are not consumed.
        """
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._failing_since is None:
            self._failing_since = now
        if not self._online.is_set():
            return attempts
        if now - self._failing_since >= self.offline_after:
            self._go_offline()
            return attempts
        attempts += 1
        if attempts >= self.max_retries:
            error.attempts = attempts
            log.warning("Giving up after %d attempts: %s", attempts, error)
            raise error
        delay = self._backoff(attempts)
        log.info("%s; retry %d/%d in %.1f s", error, attempts, self.max_retries - 1, delay)
        await asyncio.sleep(delay)
        return attempts

    def _mark_reachable(self) -> None:
        self._failing_since = None
        if not self._online.is_set():
            log.warning("Network is back, resuming")
            self._online.set()

    def _go_offline(self) -> None:
        log.error(
            "All requests failed for %.0f s - OFFLINE mode, probing every %.0f s",
            self.offline_after,
            self.offline_probe_seconds,
        )
        self._online.clear()
        if self._probe_task is None or self._probe_task.done():
            self._probe_task = asyncio.create_task(self._probe_until_online())

    async def _probe_until_online(self) -> None:
        url = self.url(self.probe_host, STATUS_PATH)
        limiter = self.limiters.get(self.probe_host)
        while not self._online.is_set():
            await asyncio.sleep(self.offline_probe_seconds)
            await limiter.acquire(STATUS_METHOD_KEY)
            try:
                response = await self._send(self.probe_host, url, None, self.keys.key)
            except NetworkError:
                continue
            limiter.update_from_headers(STATUS_METHOD_KEY, response.headers)
            if response.status_code < 500:
                self._mark_reachable()

    # ----------------------------------------------------------------- 429

    async def _handle_rate_limited(
        self, response: httpx.Response, limiter, method_key: str, url: str, attempts: int
    ) -> int:
        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
        limit_type = (response.headers.get("X-Rate-Limit-Type") or "").lower() or None
        if retry_after is not None:
            scope = "application" if limit_type == "application" else "method"
            log.warning("429 (%s) on %s %s, waiting %.0f s", limit_type or "unknown", limiter.host, method_key, retry_after)
            limiter.block(retry_after, scope, method_key)
            return attempts
        # `service` limits usually come without Retry-After: back off like a server error.
        attempts += 1
        if attempts >= self.max_retries:
            raise RateLimited(
                "Rate limited by the underlying service",
                retry_after=None,
                limit_type=limit_type,
                status=429,
                url=url,
                method_key=method_key,
            )
        delay = self._backoff(attempts)
        log.warning("429 (%s) without Retry-After on %s %s, backing off %.1f s", limit_type, limiter.host, method_key, delay)
        await asyncio.sleep(delay)
        return attempts

    # ----------------------------------------------------------------- 401 / 403

    async def _handle_rejected_key(self, used_key: str) -> bool:
        """Return True when the request should be retried (key replaced), False if the key is fine."""
        if used_key != self.keys.key:
            return True  # another request already replaced the key
        if self._key_valid_at is not None:
            valid_key, valid_at = self._key_valid_at
            if valid_key == used_key and asyncio.get_running_loop().time() - valid_at < _KEY_VALID_CACHE_SECONDS:
                return False
        if self._key_task is None:
            self._key_task = asyncio.create_task(self._recover_key(used_key))
        return await asyncio.shield(self._key_task)

    async def _recover_key(self, rejected: str) -> bool:
        self._resume.clear()
        try:
            if await self._validate_until_known(rejected):
                log.warning("Got 401/403 but the current key is valid - treating it as a forbidden endpoint")
                self._key_valid_at = (rejected, asyncio.get_running_loop().time())
                return False
            self._key_paused = True
            while True:
                candidate = await self.keys.wait_for_new_key(rejected)
                if await self._validate_until_known(candidate):
                    self.keys.set_key(candidate)
                    self.limiters.reset_all()
                    self._key_valid_at = (candidate, asyncio.get_running_loop().time())
                    log.warning("New API key accepted - resuming")
                    return True
                log.error("The key in .env is rejected too - still PAUSED")
                rejected = candidate
        finally:
            self._key_paused = False
            self._key_task = None
            self._resume.set()

    async def _validate_until_known(self, key: str) -> bool:
        """Validate `key`, retrying until Riot gives a definite yes/no answer."""
        attempt = 0
        while True:
            try:
                return await self._validate_key(key)
            except RiotApiError as exc:
                attempt += 1
                delay = self._backoff(min(attempt, 7))
                log.warning("Key validation inconclusive (%s), retrying in %.0f s", exc, delay)
                await asyncio.sleep(delay)

    async def _check_key(self, key: str) -> bool:
        """Cheap call to lol-status on `probe_host`: True on 200, False on 401/403."""
        url = self.url(self.probe_host, STATUS_PATH)
        limiter = self.limiters.get(self.probe_host)
        await limiter.acquire(STATUS_METHOD_KEY)
        response = await self._send(self.probe_host, url, None, key)
        limiter.update_from_headers(STATUS_METHOD_KEY, response.headers)
        if response.status_code == 200:
            return True
        if response.status_code in (401, 403):
            return False
        if response.status_code == 429:
            retry_after = _parse_retry_after(response.headers.get("Retry-After")) or self.backoff_base
            limiter.block(retry_after, "method", STATUS_METHOD_KEY)
            raise RateLimited("Rate limited during key validation", status=429, url=url, method_key=STATUS_METHOD_KEY)
        raise ServerError(_body_message(response), status=response.status_code, url=url, method_key=STATUS_METHOD_KEY)


def _reject_secrets(path: str, params: Mapping[str, Any] | None) -> None:
    """The key belongs only in the header; refuse anything that would put it in the URL."""
    if "api_key" in path.lower() or redact(path) != path:
        raise ValueError("API key must not be part of the request path")
    for name, value in (params or {}).items():
        if name.lower() == "api_key" or redact(str(value)) != str(value):
            raise ValueError("API key must not be passed as a query parameter")


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _body_message(response: httpx.Response) -> str:
    """Short description of an error response (Riot sends `{"status": {"message": ...}}`)."""
    try:
        message = response.json()["status"]["message"]
    except (ValueError, KeyError, TypeError):
        message = response.reason_phrase or "error"
    return f"HTTP {response.status_code}: {message}"

