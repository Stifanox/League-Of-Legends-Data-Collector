"""Builds the Riot API client from crawler settings."""

from __future__ import annotations

from pathlib import Path

from crawler.config import Settings, load_settings
from riot_client.http import RiotHttpClient
from riot_client.keys import KeyProvider


def reload_key(env_file: Path) -> str | None:
    """Current `RIOT_API_KEY` from `env_file` (+ environment), re-read on every call."""
    return load_settings(env_file, require_key=False).riot_api_key or None


def build_client(settings: Settings, env_file: Path, *, notify: bool = True, **overrides) -> RiotHttpClient:
    """`RiotHttpClient` configured from `settings`; the key is re-read from `env_file` on 401/403."""
    keys = KeyProvider(
        settings.riot_api_key,
        lambda: reload_key(env_file),
        poll_seconds=settings.key_poll_seconds,
        notify=notify,
    )
    options = dict(
        probe_host=settings.regions[0],
        safety=settings.rate_limit_safety,
        max_retries=settings.max_retries,
        offline_after=settings.offline_after_seconds,
    )
    options.update(overrides)
    return RiotHttpClient(keys, **options)
