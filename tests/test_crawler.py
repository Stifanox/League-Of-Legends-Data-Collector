"""Crawler tests: fake Riot API (MockTransport), temp data dir, virtual clock."""

from __future__ import annotations

import asyncio
import gzip
import json
import random
import re

import httpx
import pytest

from crawler.config import load_settings
from crawler.raw_writer import list_parts
from crawler.run import crawl, parse_duration, retry_parked
from crawler.state import StateStore
from riot_client.routing import TIERS
from tests.fake_riot import FakeRiot
from tests.virtual_time import run

KEY = "RGAPI-11111111-aaaa-bbbb-cccc-000000000001"
NEW_KEY = "RGAPI-22222222-aaaa-bbbb-cccc-000000000002"
APEX_ONLY = {"CHALLENGER": 2}


@pytest.fixture
def make_settings(write_env, tmp_path):
    def _make(**overrides):
        values = {
            "RIOT_API_KEY": KEY,
            "REGIONS": "eun1",
            "DATA_DIR": str(tmp_path / "data"),
            "LOG_DIR": str(tmp_path / "logs"),
            "MIN_FREE_DISK_GB": "0",
            "MATCHES_PER_PLAYER": "5",
        }
        values.update(overrides)
        env = write_env(**values)
        return load_settings(env), env

    return _make


def do_crawl(settings, env, fake, show_progress=False, **kwargs):
    return crawl(
        settings,
        env,
        show_progress=show_progress,
        install_signals=False,
        transport=httpx.MockTransport(fake),
        notify=False,
        rng=random.Random(7),
        **kwargs,
    )


def state_of(settings) -> StateStore:
    return StateStore(settings.data_dir / "state.db")


def raw_lines(settings, kind, platform="EUN1"):
    records = []
    for part in list_parts(settings.data_dir / "raw" / kind / f"platform={platform}"):
        with gzip.open(part, "rt", encoding="utf-8") as f:
            records += [json.loads(line) for line in f]
    return records


def tiers(settings, platform="EUN1"):
    with state_of(settings) as state:
        return {p.tier: p for p in state.tiers.progress(platform)}


# ---------------------------------------------------------------- strategy


def test_stratified_balances_tiers(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=6, matches_per_player=12)
    ctx = run(do_crawl(settings, env, fake, max_matches=60))
    assert ctx.total_matches == 60
    collected = [p.collected for p in tiers(settings).values()]
    assert len(collected) == len(TIERS)
    assert max(collected) - min(collected) <= settings.matches_per_player


def test_exhausted_tier_share_goes_to_others(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier={t: 4 for t in TIERS} | {"IRON": 0, "BRONZE": 1}, matches_per_player=12)
    fake.set_history("EUN1-BRONZE-0", ["EUN1_b1", "EUN1_b2"])
    run(do_crawl(settings, env, fake, max_matches=50))
    progress = tiers(settings)
    assert progress["IRON"].exhausted and progress["IRON"].collected == 0
    assert progress["BRONZE"].exhausted and progress["BRONZE"].collected == 2
    others = [p.collected for t, p in progress.items() if t not in ("IRON", "BRONZE")]
    assert sum(others) >= 48 - 2
    assert min(others) >= 5


def test_apex_fallback_pages_deeper_then_exhausts(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=12)
    ctx = run(do_crawl(settings, env, fake))  # ends on its own: everything exhausted
    assert ctx.total_matches == 24
    progress = tiers(settings)
    assert all(p.exhausted for p in progress.values())
    assert progress["CHALLENGER"].collected == 24
    assert progress["CHALLENGER"].apex_round == 2  # league loaded, then re-checked once for new players
    starts = [
        int(r.url.params["start"]) for r in fake.requests if r.url.path.endswith("EUN1-CHALLENGER-0/ids")
    ]
    assert starts == [0, 5, 10]


def test_match_seen_via_two_players_fetched_once(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=6)
    shared = fake.histories["EUN1-CHALLENGER-0"][:3]
    fake.set_history("EUN1-CHALLENGER-1", shared + ["EUN1_own1", "EUN1_own2"])
    ctx = run(do_crawl(settings, env, fake))
    assert ctx.total_matches == 8
    match_fetches = {p: n for p, n in fake.paths.items() if p.startswith("/lol/match/v5/matches/EUN1_") and "/timeline" not in p}
    assert len(match_fetches) == 8 and set(match_fetches.values()) == {1}
    assert len({r["match_id"] for r in raw_lines(settings, "matches")}) == 8


def test_snowball_follows_participants(make_settings):
    settings, env = make_settings(STRATEGY="snowball", SEED_RIOT_ID="Someone#EUNE")
    fake = FakeRiot(KEY, players_per_tier={}, matches_per_player=0)
    fake.set_history("seed-Someone", ["EUN1_s1", "EUN1_s2"])
    fake.set_history("x-EUN1_s1-1", ["EUN1_s1", "EUN1_s3"])
    run(do_crawl(settings, env, fake, max_matches=3))
    records = raw_lines(settings, "matches")
    assert {r["match_id"] for r in records} == {"EUN1_s1", "EUN1_s2", "EUN1_s3"}
    assert {r["strategy"] for r in records} == {"snowball"} and {r["seed_tier"] for r in records} == {None}


# ---------------------------------------------------------------- ranks


def test_ranks_reused_unranked_and_failed(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=3)
    # CHALLENGER-0 plays in its own 3 matches and in CHALLENGER-1's: looked up once (reuse window).
    fake.set_history("EUN1-CHALLENGER-1", fake.histories["EUN1-CHALLENGER-0"][:1] + ["EUN1_c1"])
    fake.unranked.add("EUN1-CHALLENGER-1")
    failing = fake.participants("EUN1_c1")[-1]
    fake.fail[f"by-puuid/{failing}$"] = 503
    ctx = run(do_crawl(settings, env, fake))
    assert ctx.total_matches == 4  # a failing rank lookup never blocks matches
    assert fake.count("entries/by-puuid/EUN1-CHALLENGER-0$") == 1
    statuses = {r["puuid"]: r["status"] for r in raw_lines(settings, "ranks")}
    assert statuses["EUN1-CHALLENGER-1"] == "UNRANKED"
    assert statuses["EUN1-CHALLENGER-0"] == "RANKED"
    assert failing not in statuses
    with state_of(settings) as state:
        assert [i.key for i in state.parked.items("rank")] == [failing]
        assert state.rank_queue.count("EUN1") == 0


def test_no_timelines_when_disabled(make_settings):
    settings, env = make_settings(FETCH_TIMELINES="false")
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=3)
    ctx = run(do_crawl(settings, env, fake))
    assert ctx.total_matches == 6
    assert fake.count("/timeline$") == 0
    assert raw_lines(settings, "timelines") == []


# ---------------------------------------------------------------- errors / resume


def test_failed_match_parked_then_retry_parked(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=3)
    bad = fake.histories["EUN1-CHALLENGER-0"][1]
    fake.fail[f"/matches/{bad}$"] = 500
    ctx = run(do_crawl(settings, env, fake))
    assert ctx.total_matches == 5
    with state_of(settings) as state:
        [item] = state.parked.items("match")
        assert (item.key, item.status_code) == (bad, 500)
    fake.fail.clear()
    ctx = run(retry_parked(settings, env, show_progress=False, install_signals=False,
                           transport=httpx.MockTransport(fake), notify=False))
    assert ctx.total_matches == 1
    with state_of(settings) as state:
        assert state.parked.items() == []
        assert state.matches.get(bad).status == "done"


def test_not_found_match_is_parked_but_not_retried(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=2)
    gone = fake.histories["EUN1-CHALLENGER-0"][0]
    fake.fail[f"/matches/{gone}$"] = 404
    run(do_crawl(settings, env, fake))
    run(retry_parked(settings, env, show_progress=False, install_signals=False,
                     transport=httpx.MockTransport(fake), notify=False))
    assert fake.count(f"/matches/{gone}$") == 1


def test_crash_and_resume_continues_without_refetching(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=8)

    async def crash_midway():
        task = asyncio.create_task(do_crawl(settings, env, fake))
        while fake.count(r"/matches/EUN1_\d+$") < 5:
            await asyncio.sleep(0.5)
        task.cancel()  # like a kill: no graceful shutdown
        with pytest.raises(asyncio.CancelledError):
            await task

    run(crash_midway())
    with state_of(settings) as state:
        done_before = state.summary()["platforms"]["EUN1"]["matches_done"]
    assert 0 < done_before < 16
    ctx = run(do_crawl(settings, env, fake))
    assert done_before + ctx.total_matches == 16
    fetches = {p: n for p, n in fake.paths.items() if re.fullmatch(r"/lol/match/v5/matches/EUN1_\d+", p)}
    assert len(fetches) == 16
    assert sum(n - 1 for n in fetches.values()) <= 1  # at most the one in flight at the crash
    assert len({r["match_id"] for r in raw_lines(settings, "matches")}) == 16


def test_key_rejected_mid_run_pauses_everything_and_resumes(make_settings, write_env):
    settings, env = make_settings(KEY_POLL_SECONDS="30")
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=6)
    seen = {}

    async def scenario():
        task = asyncio.create_task(do_crawl(settings, env, fake))
        while fake.count(r"/matches/EUN1_\d+$") < 3:
            await asyncio.sleep(0.5)
        fake.valid_keys = {NEW_KEY}  # the dev key expired
        await asyncio.sleep(100)
        seen["requests_while_paused"] = len(fake.requests)
        await asyncio.sleep(100)
        seen["requests_later"] = len(fake.requests)
        # The user puts a new key into .env; it is picked up at the next poll.
        env.write_text(env.read_text().replace(KEY, NEW_KEY))
        return await task

    ctx = run(scenario())
    assert ctx.total_matches == 12
    # While paused nothing is sent: .env polls only call the API once the key in it changed.
    assert seen["requests_later"] == seen["requests_while_paused"]
    tokens = [r.headers["X-Riot-Token"] for r in fake.requests]
    first_new = tokens.index(NEW_KEY)
    assert set(tokens[first_new:]) == {NEW_KEY}


# ---------------------------------------------------------------- modes


def test_matches_only_queues_ranks_then_ranks_only_looks_them_up(make_settings):
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=3)
    ctx = run(do_crawl(settings, env, fake, mode="matches-only"))
    assert ctx.total_matches == 6 and ctx.total_ranks == 0
    assert fake.count("entries/by-puuid/") == 0
    with state_of(settings) as state:
        queued = state.rank_queue.count("EUN1")
    assert queued == len({p for m in fake._participants for p in fake.participants(m)})

    match_requests = fake.count("/lol/match/v5/")
    league_requests = fake.count("/lol/league/v4/(entries/RANKED|\\w+leagues)")
    ctx = run(do_crawl(settings, env, fake, mode="ranks-only"))
    assert ctx.total_matches == 0 and ctx.total_ranks == queued
    assert fake.count("/lol/match/v5/") == match_requests  # no new players or matches
    assert fake.count("/lol/league/v4/(entries/RANKED|\\w+leagues)") == league_requests  # no seeding
    with state_of(settings) as state:
        assert state.rank_queue.count("EUN1") == 0


def test_mode_defaults_to_setting(make_settings):
    settings, env = make_settings(CRAWL_MODE="matches-only")
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=2)
    run(do_crawl(settings, env, fake))
    assert fake.count("entries/by-puuid/") == 0


def test_unknown_mode_rejected(make_settings):
    settings, env = make_settings()
    with pytest.raises(ValueError):
        run(do_crawl(settings, env, FakeRiot(KEY), mode="everything"))


def test_progress_logs_status_without_tty(make_settings, caplog):
    caplog.set_level("INFO")
    settings, env = make_settings()
    fake = FakeRiot(KEY, players_per_tier=APEX_ONLY, matches_per_player=3)
    run(do_crawl(settings, env, fake, show_progress=True))
    lines = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("progress: ") and "EUN1 m:" in m and "r/s" in m for m in lines)
    assert any(m.startswith("EUN1 tiers: ") and "C 6" in m for m in lines)


def test_parse_duration():
    assert parse_duration("48h") == 172800
    assert parse_duration("90m") == 5400
    assert parse_duration("2d") == 172800
    assert parse_duration("30") == 30
    with pytest.raises(ValueError):
        parse_duration("soon")

