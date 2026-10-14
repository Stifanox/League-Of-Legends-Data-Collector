"""Thin wrappers around Riot API endpoints. They return raw parsed JSON (no lossy models)."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol

from riot_client.errors import InvalidResponse


class JsonGetter(Protocol):
    """What the wrappers need from `RiotHttpClient`."""

    async def get_json(
        self, host: str, path: str, params: Mapping[str, Any] | None = None, *, method_key: str
    ) -> Any: ...


def expect(value: Any, check: Callable[[Any], bool], what: str, method_key: str) -> Any:
    """Return `value` if it has the expected shape, else raise `InvalidResponse`."""
    if not check(value):
        raise InvalidResponse(f"Unexpected response shape, expected {what}", method_key=method_key)
    return value
