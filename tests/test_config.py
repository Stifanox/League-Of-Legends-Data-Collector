"""Tests for crawler.config."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from crawler.config import DEFAULTS, ConfigError, Settings, load_settings

KEY = "RGAPI-12345678-abcd-ef01-2345-6789abcdef01"


def test_defaults_with_only_key(write_env):
    s = load_settings(write_env(RIOT_API_KEY=KEY))
    assert s.riot_api_key == KEY
    assert s.regions == ("eun1", "na1", "kr")
    assert s.queue_id == 420
    assert s.window_days == 14
    assert s.split_reset_date is None
    assert s.fetch_timelines is True
    assert s.matches_per_player == 5
    assert s.crawl_mode == "full"
    assert s.strategy == "stratified"
    assert s.seed_riot_id is None
    assert s.rank_reuse_hours == 6
    assert s.rate_limit_safety == 0.9
    assert s.key_poll_seconds == 30
    assert s.max_retries == 5
    assert s.offline_after_seconds == 300
    assert s.min_free_disk_gb == 2
    assert s.raw_roll_mb == 128
    assert s.data_dir == Path("data")
    assert s.log_dir == Path("logs")


def test_example_file_lists_every_variable():
    example = Path(__file__).parent.parent / ".env.example"
    names = {
        line.split("=", 1)[0]
        for line in example.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    assert names == set(DEFAULTS)


def test_example_file_loads_with_key(tmp_path):
    example = (Path(__file__).parent.parent / ".env.example").read_text(encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(example.replace("RIOT_API_KEY=", f"RIOT_API_KEY={KEY}"), encoding="utf-8")
    assert load_settings(env).regions == ("eun1", "na1", "kr")


def test_list_parsing_trims_lowercases_and_dedupes(write_env):
    s = load_settings(write_env(RIOT_API_KEY=KEY, REGIONS=" EUN1, kr,,eun1 ,NA1"))
    assert s.regions == ("eun1", "kr", "na1")


@pytest.mark.parametrize(
    ("text", "expected"),
    [("true", True), ("TRUE", True), ("1", True), ("yes", True), ("on", True),
     ("false", False), ("False", False), ("0", False), ("no", False), ("off", False)],
)
def test_bool_parsing(write_env, text, expected):
    assert load_settings(write_env(RIOT_API_KEY=KEY, FETCH_TIMELINES=text)).fetch_timelines is expected


def test_typed_values(write_env):
    s = load_settings(
        write_env(
            RIOT_API_KEY=KEY,
            SPLIT_RESET_DATE="2026-09-24",
            RATE_LIMIT_SAFETY="0.75",
            STRATEGY="Snowball",
            SEED_RIOT_ID="Some Name#EUNE",
            DATA_DIR="/tmp/lol",
        )
    )
    assert s.split_reset_date == date(2026, 9, 24)
    assert s.rate_limit_safety == 0.75
    assert s.strategy == "snowball"
    assert s.seed_riot_id == "Some Name#EUNE"
    assert s.data_dir == Path("/tmp/lol")


def test_empty_value_falls_back_to_default(write_env):
    assert load_settings(write_env(RIOT_API_KEY=KEY, WINDOW_DAYS="")).window_days == 14


def test_environment_overrides_file(write_env, monkeypatch):
    env = write_env(RIOT_API_KEY=KEY, WINDOW_DAYS="7")
    monkeypatch.setenv("WINDOW_DAYS", "3")
    assert load_settings(env).window_days == 3


def test_missing_file_uses_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("RIOT_API_KEY", KEY)
    assert load_settings(tmp_path / "does-not-exist.env").riot_api_key == KEY


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({}, "RIOT_API_KEY is not set"),
        ({"RIOT_API_KEY": KEY, "REGIONS": "eun1,xx9"}, "unknown platform(s) xx9"),
        ({"RIOT_API_KEY": KEY, "REGIONS": ",,"}, "at least one platform"),
        ({"RIOT_API_KEY": KEY, "STRATEGY": "random"}, "STRATEGY must be one of"),
        ({"RIOT_API_KEY": KEY, "CRAWL_MODE": "turbo"}, "CRAWL_MODE must be one of"),
        ({"RIOT_API_KEY": KEY, "STRATEGY": "snowball"}, "requires SEED_RIOT_ID"),
        ({"RIOT_API_KEY": KEY, "SEED_RIOT_ID": "NoTag"}, "GameName#TAG"),
        ({"RIOT_API_KEY": KEY, "FETCH_TIMELINES": "maybe"}, "FETCH_TIMELINES must be true or false"),
        ({"RIOT_API_KEY": KEY, "WINDOW_DAYS": "two"}, "WINDOW_DAYS must be an integer"),
        ({"RIOT_API_KEY": KEY, "WINDOW_DAYS": "0"}, "WINDOW_DAYS must be >= 1"),
        ({"RIOT_API_KEY": KEY, "MATCHES_PER_PLAYER": "101"}, "MATCHES_PER_PLAYER must be <= 100"),
        ({"RIOT_API_KEY": KEY, "RATE_LIMIT_SAFETY": "1.5"}, "RATE_LIMIT_SAFETY must be <= 1"),
        ({"RIOT_API_KEY": KEY, "SPLIT_RESET_DATE": "24.09.2026"}, "SPLIT_RESET_DATE must be an ISO date"),
    ],
)
def test_validation_errors(write_env, values, message):
    with pytest.raises(ConfigError) as info:
        load_settings(write_env(**values))
    assert message in str(info.value)


def test_validation_reports_all_problems_at_once(write_env):
    with pytest.raises(ConfigError) as info:
        load_settings(write_env(STRATEGY="random", WINDOW_DAYS="x"))
    assert len(info.value.problems) == 3


def test_key_optional_when_not_required(write_env):
    assert load_settings(write_env(), require_key=False).riot_api_key == ""


def test_rereading_picks_up_changed_file(write_env):
    env = write_env(RIOT_API_KEY=KEY, REGIONS="eun1")
    first = load_settings(env)
    new_key = "RGAPI-ffffffff-0000-1111-2222-333333333333"
    write_env(RIOT_API_KEY=new_key, REGIONS="kr")
    second = load_settings(env)
    assert (first.riot_api_key, first.regions) == (KEY, ("eun1",))
    assert (second.riot_api_key, second.regions) == (new_key, ("kr",))


def test_key_never_in_repr_or_describe(write_env):
    s = load_settings(write_env(RIOT_API_KEY=KEY))
    assert KEY not in repr(s)
    described = dict(s.describe())
    assert described["RIOT_API_KEY"] == "RGAPI-****"
    assert all(KEY not in value for value in described.values())


def test_settings_is_frozen(write_env):
    s = load_settings(write_env(RIOT_API_KEY=KEY))
    with pytest.raises(AttributeError):
        s.regions = ("kr",)  # type: ignore[misc]
    assert isinstance(s, Settings)
