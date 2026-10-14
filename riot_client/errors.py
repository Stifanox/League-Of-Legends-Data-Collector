"""Exceptions raised by the Riot API client (DESIGN §7)."""

from __future__ import annotations

import re
from typing import ClassVar

KEY_PATTERN = re.compile(r"RGAPI-[0-9a-fA-F-]+")
_API_KEY_PARAM = re.compile(r"(api_key=)[^&\s]*", re.IGNORECASE)


def redact(text: str) -> str:
    """Remove anything that looks like an API key from `text`."""
    return _API_KEY_PARAM.sub(r"\1****", KEY_PATTERN.sub("RGAPI-****", text))


class RiotApiError(Exception):
    """Base error. `retryable` tells callers whether trying again later can help."""

    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        url: str | None = None,
        method_key: str | None = None,
    ) -> None:
        self.message = redact(message)
        self.status = status
        self.url = redact(url) if url else None
        self.method_key = method_key
        self.attempts = 1
        super().__init__(self.message)

    def __str__(self) -> str:
        parts = [self.message]
        if self.status is not None:
            parts.append(f"status={self.status}")
        if self.method_key:
            parts.append(f"method={self.method_key}")
        if self.url:
            parts.append(f"url={self.url}")
        return " ".join(parts)


class BadRequest(RiotApiError):
    """400: the request itself is wrong; retrying will not help."""


class NotFound(RiotApiError):
    """404: match removed, unknown id, etc."""


class KeyInvalid(RiotApiError):
    """401/403 caused by a missing, invalid or expired API key."""


class Forbidden(RiotApiError):
    """403 although the key is valid (e.g. endpoint not available for this key)."""


class RateLimited(RiotApiError):
    """429 that could not be resolved by waiting (only `service` limits without Retry-After)."""

    retryable = True

    def __init__(self, message: str, *, retry_after: float | None = None, limit_type: str | None = None, **kwargs) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after
        self.limit_type = limit_type


class ServerError(RiotApiError):
    """5xx from Riot."""

    retryable = True


class NetworkError(RiotApiError):
    """Timeout or connection problem; no HTTP response."""

    retryable = True


class InvalidResponse(RiotApiError):
    """200 with a body that is not the expected JSON."""
