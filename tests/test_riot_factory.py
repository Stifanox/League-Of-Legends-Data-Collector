"""Tests for crawler.riot (client built from settings)."""

from __future__ import annotations

from crawler.config import load_settings
from crawler.riot import build_client, reload_key
from tests.virtual_time import run

KEY = "RGAPI-12345678-abcd-ef01-2345-6789abcdef01"
NEW_KEY = "RGAPI-ffffffff-0000-1111-2222-333333333333"


def test_build_client_uses_settings_and_rereads_env(write_env):
    env = write_env(RIOT_API_KEY=KEY, REGIONS="kr,eun1", MAX_RETRIES="7", RATE_LIMIT_SAFETY="0.8")
    settings = load_settings(env)

    async def scenario():
        async with build_client(settings, env) as client:
            write_env(RIOT_API_KEY=NEW_KEY, REGIONS="kr,eun1")
            return client.probe_host, client.max_retries, client.limiters.safety, client.keys.key, client.keys._try_reload()

    assert run(scenario()) == ("kr", 7, 0.8, KEY, NEW_KEY)


def test_reload_key_missing_key_is_none(write_env):
    assert reload_key(write_env(REGIONS="eun1")) is None
