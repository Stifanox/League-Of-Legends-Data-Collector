"""Tests for crawler.state."""

from __future__ import annotations

import sqlite3

import pytest

from crawler.state import StateStore
from crawler.state.schema import MIGRATIONS


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def state(tmp_path, clock):
    with StateStore(tmp_path / "state.db", clock=clock) as s:
        yield s


def enqueue(state, *ids, platform="EUN1", tier="GOLD"):
    return state.matches.enqueue(platform, ids, seed_tier=tier, seed_puuid="seed", strategy="stratified")


# ---------------------------------------------------------------- database / schema


def test_wal_mode_and_schema_version(state):
    assert state.db.scalar("PRAGMA journal_mode") == "wal"
    assert state.schema_version == len(MIGRATIONS)


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "state.db"
    db = sqlite3.connect(path)
    db.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    db.close()
    with pytest.raises(RuntimeError, match="newer than this code"):
        StateStore(path)


def test_nested_transaction_rolls_back_as_one(state):
    with pytest.raises(ZeroDivisionError):
        with state.db.transaction():
            enqueue(state, "EUN1_1")  # opens its own (nested) transaction
            1 / 0
    assert not state.matches.is_seen("EUN1_1")


def test_meta(state):
    assert state.meta.get("x") is None
    state.meta.set("x", "1")
    state.meta.set("x", "2")
    assert state.meta.get("x") == "2"


# ---------------------------------------------------------------- matches


def test_enqueue_dedupes_and_returns_new_only(state):
    assert enqueue(state, "EUN1_1", "EUN1_2") == ["EUN1_1", "EUN1_2"]
    assert enqueue(state, "EUN1_2", "EUN1_3", "EUN1_3") == ["EUN1_3"]
    assert state.matches.is_seen("EUN1_1") and not state.matches.is_seen("EUN1_9")
    assert [m.match_id for m in state.matches.queued("EUN1")] == ["EUN1_1", "EUN1_2", "EUN1_3"]


def test_match_round_trip_done(state):
    enqueue(state, "EUN1_1")
    state.matches.mark_fetched("EUN1_1")
    m = state.matches.get("EUN1_1")
    assert (m.match_done, m.timeline_done, m.seed_tier, m.seed_puuid, m.strategy) == (
        True, False, "GOLD", "seed", "stratified"
    )
    assert state.matches.queued("EUN1")[0].match_done  # half-done stays queued for resume
    state.matches.mark_timeline_fetched("EUN1_1")
    assert state.finish_match("EUN1_1") is True
    assert state.finish_match("EUN1_1") is False  # counted once
    assert state.finish_match("EUN1_404") is False
    assert state.matches.queued("EUN1") == []
    assert state.matches.get("EUN1_1").status == "done"
    assert {p.tier: p.collected for p in state.tiers.progress("EUN1")}["GOLD"] == 1


def test_park_and_requeue_match(state):
    enqueue(state, "EUN1_1", "EUN1_2")
    state.park("match", "EUN1", "EUN1_1", "ServerError: 503", 503)
    state.park("match", "EUN1", "EUN1_1", "ServerError: 502", 502)
    assert [m.match_id for m in state.matches.queued("EUN1")] == ["EUN1_2"]
    [item] = state.parked.items("match")
    assert (item.key, item.attempts, item.status_code, item.error) == ("EUN1_1", 2, 502, "ServerError: 502")
    state.matches.requeue("EUN1_1")
    assert {m.match_id for m in state.matches.queued("EUN1")} == {"EUN1_1", "EUN1_2"}
    state.finish_match("EUN1_1")
    assert state.parked.items("match") == []  # finishing clears the parked entry


def test_park_rejects_unknown_kind(state):
    with pytest.raises(ValueError):
        state.park("player", "EUN1", "x", "boom")


def test_queued_matches_per_platform(state):
    enqueue(state, "EUN1_1")
    enqueue(state, "KR_1", platform="KR")
    assert [m.match_id for m in state.matches.queued("KR")] == ["KR_1"]


# ---------------------------------------------------------------- players


def test_players_add_claim_visit(state):
    assert state.players.add("EUN1", ["a", "b"], tier="GOLD", source="league") == ["a", "b"]
    assert state.players.add("EUN1", ["b", "c"], tier="GOLD", source="league") == ["c"]
    assert state.players.known("EUN1", ["a", "z"]) == {"a"}
    assert state.players.next_unvisited("EUN1", "GOLD").puuid == "a"
    state.players.mark_visited("EUN1", "a", next_start=5)
    assert state.players.next_unvisited("EUN1", "GOLD").puuid == "b"
    assert state.players.next_unvisited("EUN1").puuid == "b"
    assert state.players.next_unvisited("EUN1", "SILVER") is None
    assert state.players.next_unvisited("EUN1", "GOLD", random=True).puuid in {"b", "c"}
    assert state.players.count("EUN1", "GOLD", unvisited=True) == 2
    assert state.players.count("EUN1") == 3
    assert state.players.count_visited("EUN1") == 1


def test_revisit_with_paging_offset(state, clock):
    state.players.add("EUN1", ["x", "y"], tier="MASTER", source="apex")
    state.players.mark_visited("EUN1", "x", next_start=5)
    clock.now += 10
    state.players.mark_visited("EUN1", "y", next_start=5)
    p = state.players.next_revisit("EUN1", "MASTER")
    assert (p.puuid, p.next_start, p.visits) == ("x", 5, 1)
    clock.now += 10
    state.players.mark_visited("EUN1", "x", next_start=10, exhausted=True)
    assert state.players.next_revisit("EUN1", "MASTER").puuid == "y"
    state.players.mark_visited("EUN1", "y", next_start=10, exhausted=True)
    assert state.players.next_revisit("EUN1", "MASTER") is None


# ---------------------------------------------------------------- tiers / league pages


def test_least_collected_tier_skips_exhausted(state):
    state.tiers.ensure("EUN1", ["IRON", "GOLD", "CHALLENGER"])
    assert state.tiers.least_collected("EUN1") == "IRON"  # tie -> lowest tier
    enqueue(state, "EUN1_1", tier="IRON")
    state.finish_match("EUN1_1")
    assert state.tiers.least_collected("EUN1") == "GOLD"
    state.tiers.set_exhausted("EUN1", "GOLD")
    assert state.tiers.least_collected("EUN1") == "CHALLENGER"
    state.tiers.set_exhausted("EUN1", "CHALLENGER")
    assert state.tiers.least_collected("EUN1") == "IRON"
    state.tiers.set_exhausted("EUN1", "IRON")
    assert state.tiers.least_collected("EUN1") is None


def test_ensure_tiers_defaults_to_all_and_keeps_progress(state):
    state.tiers.ensure("EUN1")
    assert [p.tier for p in state.tiers.progress("EUN1")][:2] == ["IRON", "BRONZE"]
    assert len(state.tiers.progress("EUN1")) == 10
    assert state.tiers.bump_apex_round("EUN1", "MASTER") == 1
    state.tiers.ensure("EUN1")
    assert state.tiers.bump_apex_round("EUN1", "MASTER") == 2


def test_league_pages(state):
    state.league_pages.record("EUN1", "GOLD", "II", 1, 205)
    state.league_pages.record("EUN1", "GOLD", "II", 40, 0)
    state.league_pages.record("EUN1", "GOLD", "II", 1, 204)
    assert state.league_pages.pages("EUN1", "GOLD", "II") == {1: 204, 40: 0}
    assert state.league_pages.pages("EUN1", "GOLD", "I") == {}


# ---------------------------------------------------------------- ranks


def test_rank_reuse_window(state, clock):
    assert not state.rank_lookups.recent("EUN1", "p", 6)
    state.record_rank_lookup("EUN1", "p", "RANKED")
    clock.now += 5 * 3600
    assert state.rank_lookups.recent("EUN1", "p", 6)
    assert not state.rank_lookups.recent("KR", "p", 6)
    clock.now += 2 * 3600
    assert not state.rank_lookups.recent("EUN1", "p", 6)


def test_rank_queue_and_parking(state):
    assert state.rank_queue.add("EUN1", ["a", "b", "a"]) == 2
    assert state.rank_queue.add("EUN1", ["b", "c"]) == 1
    assert state.rank_queue.next("EUN1") == ["a", "b", "c"]
    state.record_rank_lookup("EUN1", "a", "UNRANKED")
    state.park("rank", "EUN1", "b", "ServerError: 500", 500)
    assert state.rank_queue.next("EUN1") == ["c"]
    assert [i.key for i in state.parked.items("rank")] == ["b"]
    state.record_rank_lookup("EUN1", "b", "RANKED")
    assert state.parked.items("rank") == []
    assert state.rank_lookups.count("EUN1") == 2


# ---------------------------------------------------------------- restart / summary


def test_restart_sees_prior_state(tmp_path, clock):
    path = tmp_path / "state.db"
    with StateStore(path, clock=clock) as first:
        enqueue(first, "EUN1_1", "EUN1_2")
        first.matches.mark_fetched("EUN1_1")
        first.finish_match("EUN1_2")
        first.players.add("EUN1", ["a"], tier="GOLD", source="league")
        first.park("ids", "EUN1", "a", "boom")
        first.meta.set("run", "1")
    with StateStore(path, clock=clock) as second:
        assert second.matches.is_seen("EUN1_1")
        [queued] = second.matches.queued("EUN1")
        assert queued.match_id == "EUN1_1" and queued.match_done
        assert second.matches.get("EUN1_2").status == "done"
        assert second.players.next_unvisited("EUN1").puuid == "a"
        assert [i.kind for i in second.parked.items()] == ["ids"]
        assert second.meta.get("run") == "1"


def test_summary(state):
    state.tiers.ensure("EUN1")
    enqueue(state, "EUN1_1", "EUN1_2", "EUN1_3")
    state.matches.mark_timeline_fetched("EUN1_1")
    state.finish_match("EUN1_1")
    state.park("match", "EUN1", "EUN1_2", "x")
    state.rank_queue.add("EUN1", ["a"])
    summary = state.summary()
    eun1 = summary["platforms"]["EUN1"]
    assert (eun1["matches_done"], eun1["matches_queued"], eun1["matches_parked"]) == (1, 1, 1)
    assert eun1["timelines_done"] == 1 and eun1["rank_queue"] == 1
    assert summary["parked"] == {"match": 1}
