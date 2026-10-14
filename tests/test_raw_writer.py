"""Tests for crawler.raw_writer and crawler.storage."""

from __future__ import annotations

import gzip
import json
import re

import pytest

from crawler.raw_writer import (
    RawWriter,
    list_parts,
    match_record,
    rank_record,
    read_records,
    repair_tail,
    timeline_record,
    utc_now_iso,
)
from crawler.storage import Storage
from tests.conftest import FIXTURES_DIR


def fixture(name):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_writes_readable_jsonl_gz(tmp_path):
    with RawWriter(tmp_path, "matches", fsync=False) as w:
        path = w.write("eun1", {"i": 1, "name": "Zażółć"})
        w.write("EUN1", {"i": 2})
    assert path == tmp_path / "matches" / "platform=EUN1" / "part-0001.jsonl.gz"
    assert lines(path) == [{"i": 1, "name": "Zażółć"}, {"i": 2}]
    raw = gzip.decompress(path.read_bytes()).decode()
    assert "Zażółć" in raw  # ensure_ascii=False
    assert ', "' not in raw  # compact separators


def test_platforms_get_separate_folders(tmp_path):
    with RawWriter(tmp_path, "ranks", fsync=False) as w:
        a = w.write("EUN1", {"p": 1})
        b = w.write("KR", {"p": 2})
    assert a.parent.name == "platform=EUN1" and b.parent.name == "platform=KR"


def test_rolls_at_size(tmp_path):
    big = {"blob": "x" * 50_000 + "".join(chr(0x4E00 + i) for i in range(20_000))}  # poorly compressible
    with RawWriter(tmp_path, "timelines", roll_mb=0.05, fsync=False) as w:
        paths = [w.write("EUN1", {**big, "i": i}) for i in range(4)]
    parts = list_parts(tmp_path / "timelines" / "platform=EUN1")
    assert len(parts) > 1
    assert [r["i"] for p in parts for r in lines(p)] == [0, 1, 2, 3]
    assert paths[0].name == "part-0001.jsonl.gz"


def test_numbering_continues_after_restart(tmp_path):
    with RawWriter(tmp_path, "matches", fsync=False) as w:
        first = w.write("EUN1", {"run": 1})
    with RawWriter(tmp_path, "matches", fsync=False) as w:
        second = w.write("EUN1", {"run": 2})
    assert (first.name, second.name) == ("part-0001.jsonl.gz", "part-0002.jsonl.gz")
    assert lines(first) == [{"run": 1}]  # never appended to


def test_crash_without_close_keeps_flushed_records(tmp_path):
    w = RawWriter(tmp_path, "matches")
    path = w.write("EUN1", {"i": 1})
    w.write("EUN1", {"i": 2})
    # Simulated crash: the writer is never closed.
    assert lines(path) == [{"i": 1}, {"i": 2}]


def test_half_written_record_is_trimmed_on_restart(tmp_path):
    with RawWriter(tmp_path, "matches", fsync=False) as w:
        path = w.write("EUN1", {"i": 1})
        w.write("EUN1", {"i": 2})
    member = gzip.compress(b'{"i":3}\n')
    with open(path, "ab") as f:
        f.write(member[: len(member) // 2])  # crash mid-write
    assert [r["i"] for r in read_records(path)] == [1, 2]  # reader tolerates the damaged tail
    with RawWriter(tmp_path, "matches", fsync=False) as w:
        w.write("EUN1", {"i": 4})
    assert lines(path) == [{"i": 1}, {"i": 2}]  # trimmed: valid gzip again
    assert repair_tail(path) == 0


def test_repair_tail_on_large_file(tmp_path):
    path = tmp_path / "part-0001.jsonl.gz"
    with open(path, "wb") as f:
        for i in range(300):
            f.write(gzip.compress(json.dumps({"i": i, "pad": "y" * 3000}).encode() + b"\n"))
        size = f.tell()
        f.write(b"\x1f\x8b\x08garbage")
    assert repair_tail(path) == len(b"\x1f\x8b\x08garbage")
    assert path.stat().st_size == size


def test_unknown_kind_rejected(tmp_path):
    with pytest.raises(ValueError):
        RawWriter(tmp_path, "players")


def test_envelopes():
    data = fixture("match.json")
    m = match_record("EUN1_1", "EUN1", data, seed_tier="GOLD", seed_puuid="p", strategy="stratified")
    assert list(m) == ["match_id", "platform_id", "fetched_at", "seed_tier", "seed_puuid", "strategy", "data"]
    assert m["data"] is data
    t = timeline_record("EUN1_1", "EUN1", {"info": {}})
    assert list(t) == ["match_id", "platform_id", "fetched_at", "data"]
    r = rank_record("p", "EUN1", "UNRANKED", [])
    assert list(r) == ["puuid", "platform_id", "fetched_at", "status", "entries"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", utc_now_iso())


def test_storage_writes_raw_then_state(tmp_path):
    match = fixture("match.json")
    match_id = match["metadata"]["matchId"]
    with Storage(tmp_path, fsync=False) as storage:
        storage.state.matches.enqueue("EUN1", [match_id], seed_tier="CHALLENGER", seed_puuid="p", strategy="stratified")
        storage.persist_match("EUN1", match_id, match, seed_tier="CHALLENGER", seed_puuid="p", strategy="stratified")
        storage.persist_timeline("EUN1", match_id, fixture("timeline.json"))
        assert storage.state.finish_match(match_id)
        assert storage.persist_rank("EUN1", "p", fixture("entries_by_puuid_ranked.json")) == "RANKED"
        assert storage.persist_rank("EUN1", "q", fixture("entries_by_puuid_empty.json")) == "UNRANKED"
        summary = storage.state.summary()["platforms"]["EUN1"]
    assert (summary["matches_done"], summary["timelines_done"], summary["rank_lookups"]) == (1, 1, 2)
    [record] = lines(tmp_path / "raw" / "matches" / "platform=EUN1" / "part-0001.jsonl.gz")
    assert record["match_id"] == match_id and record["data"] == match
    ranks = lines(tmp_path / "raw" / "ranks" / "platform=EUN1" / "part-0001.jsonl.gz")
    assert [r["status"] for r in ranks] == ["RANKED", "UNRANKED"]


def test_storage_raw_failure_leaves_state_untouched(tmp_path, monkeypatch):
    with Storage(tmp_path, fsync=False) as storage:
        storage.state.matches.enqueue("EUN1", ["EUN1_1"], seed_tier=None, seed_puuid=None, strategy="snowball")

        def disk_full(*args, **kwargs):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(storage.matches, "write", disk_full)
        with pytest.raises(OSError):
            storage.persist_match("EUN1", "EUN1_1", {}, seed_tier=None, seed_puuid=None, strategy="snowball")
        assert not storage.state.matches.get("EUN1_1").match_done
