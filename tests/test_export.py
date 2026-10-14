"""Tests for crawler.export (fixtures, temp dirs, no API)."""

from __future__ import annotations

import csv
import json

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from crawler.export.flatten import RankIndex, flatten_match, flatten_rank, flatten_timeline, patch
from crawler.export.run import export
from crawler.export.schemas import PARTICIPANTS, TABLES, Column, Table
from crawler.export.writers import TsvWriter, tsv_value
from crawler.raw_writer import RawWriter, match_record, rank_record, timeline_record
from tests.conftest import FIXTURES_DIR


def fixture(name):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


MATCH = fixture("match.json")
TIMELINE = fixture("timeline.json")
MATCH_ID = MATCH["metadata"]["matchId"]
GAME_END = MATCH["info"]["gameEndTimestamp"]
PUUID = MATCH["metadata"]["participants"][0]
HOUR = 3_600_000


def iso(ms: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(ms / 1000, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def ranked(tier="GOLD", division="II", lp=50):
    return [{"queueType": "RANKED_SOLO_5x5", "tier": tier, "rank": division, "leaguePoints": lp, "wins": 10, "losses": 8,
             "puuid": PUUID, "hotStreak": False, "veteran": True, "freshBlood": False, "inactive": False}]


def rank_row(puuid, fetched_ms, entries, status=None):
    record = rank_record(puuid, "EUN1", status or ("RANKED" if entries else "UNRANKED"), entries, fetched_at=iso(fetched_ms))
    return flatten_rank(record)


def match_rec(data=MATCH, match_id=MATCH_ID, platform="EUN1"):
    return match_record(match_id, platform, data, seed_tier="CHALLENGER", seed_puuid=PUUID, strategy="stratified")


# ---------------------------------------------------------------- flatten


def test_patch_derivation():
    assert patch("16.20.824.8524") == "16.20"
    assert patch("9.3.1") == "9.3"
    assert patch(None) is None


def test_flatten_fixture_match():
    rows = flatten_match(match_rec())
    [m] = rows["matches"]
    assert len(rows["participants"]) == 10 and len(rows["teams"]) == 2
    assert m["match_id"] == MATCH_ID and m["platform_id"] == "EUN1" and m["patch"] == "16.20"
    assert m["queue_id"] == 420 and m["game_end"] == GAME_END and m["duration_s"] == MATCH["info"]["gameDuration"]
    winner = next(t["teamId"] for t in MATCH["info"]["teams"] if t["win"])
    assert m["winning_team"] == winner
    assert (m["seed_tier"], m["strategy"]) == ("CHALLENGER", "stratified")

    p = rows["participants"][0]
    src = MATCH["info"]["participants"][0]
    assert (p["puuid"], p["champion_name"], p["kills"], p["item6"]) == (src["puuid"], src["championName"], src["kills"], src["item6"])
    assert p["total_damage_dealt_to_champions"] == src["totalDamageDealtToChampions"]
    assert p["time_ccing_others"] == src["timeCCingOthers"]
    assert p["ch_kda"] == pytest.approx(src["challenges"]["kda"])
    assert p["primary_style"] == src["perks"]["styles"][0]["style"]
    assert p["perk0"] == src["perks"]["styles"][0]["selections"][0]["perk"]
    assert p["perk5"] == src["perks"]["styles"][1]["selections"][1]["perk"]
    assert p["stat_perk_offense"] == src["perks"]["statPerks"]["offense"]
    assert p["rank_status"] == "NOT_FETCHED"
    # Every raw-sourced participant column exists in the fixture (catches typos in schemas).
    missing = [c.name for c in PARTICIPANTS.columns if c.source and p[c.name] is None]
    assert missing == []

    team = rows["teams"][0]
    assert len(team["bans"]) == 5 and all(isinstance(b, int) for b in team["bans"])
    assert team["tower_kills"] == MATCH["info"]["teams"][0]["objectives"]["tower"]["kills"]


def test_flatten_fixture_timeline():
    rows = flatten_timeline(timeline_record(MATCH_ID, "EUN1", TIMELINE), "16.20")
    frames = TIMELINE["info"]["frames"]
    assert len(rows["timeline_participant_frames"]) == len(frames) * 10
    assert len(rows["timeline_events"]) == sum(len(f["events"]) for f in frames)
    frame = rows["timeline_participant_frames"][10 * 5]  # minute 5, participant 1
    assert (frame["minute"], frame["participant_id"], frame["team_id"]) == (5, 1, 100)
    assert frame["puuid"] == TIMELINE["info"]["participants"][0]["puuid"]
    assert frame["total_gold"] == frames[5]["participantFrames"]["1"]["totalGold"]
    assert frame["health_max"] is not None and frame["total_damage_done_to_champions"] is not None
    kill = next(e for e in rows["timeline_events"] if e["type"] == "CHAMPION_KILL" and e["assisting_participant_ids"])
    assert all(isinstance(i, int) for i in kill["assisting_participant_ids"])
    assert kill["x"] is not None and kill["victim_id"] is not None
    assert [e["event_index"] for e in rows["timeline_events"]][:3] == [0, 1, 2]


# ---------------------------------------------------------------- rank join


def test_rank_join_prefers_first_lookup_after_game():
    index = RankIndex()
    index.add(rank_row(PUUID, GAME_END - 5 * HOUR, ranked("SILVER")))
    index.add(rank_row(PUUID, GAME_END + 3 * HOUR, ranked("GOLD")))
    index.add(rank_row(PUUID, GAME_END + 30 * HOUR, ranked("PLATINUM")))
    rank = index.lookup(PUUID, GAME_END)
    assert (rank["rank_status"], rank["tier"], rank["rank_lag_hours"]) == ("RANKED", "GOLD", 3.0)


def test_rank_join_falls_back_to_latest_before_game():
    index = RankIndex()
    index.add(rank_row(PUUID, GAME_END - 9 * HOUR, ranked("SILVER")))
    index.add(rank_row(PUUID, GAME_END - 2 * HOUR, ranked("GOLD")))
    rank = index.lookup(PUUID, GAME_END)
    assert (rank["tier"], rank["rank_lag_hours"]) == ("GOLD", -2.0)


def test_rank_join_not_fetched_and_unranked():
    index = RankIndex()
    index.add(rank_row("someone-else", GAME_END, ranked()))
    assert index.lookup(PUUID, GAME_END)["rank_status"] == "NOT_FETCHED"
    index.add(rank_row(PUUID, GAME_END + HOUR, []))
    rank = index.lookup(PUUID, GAME_END)
    assert (rank["rank_status"], rank["tier"], rank["lp"]) == ("UNRANKED", None, None)


def test_participants_get_rank_columns():
    index = RankIndex()
    index.add(rank_row(PUUID, GAME_END + HOUR, ranked("EMERALD", "I", 77)))
    p = flatten_match(match_rec(), index)["participants"][0]
    assert (p["tier"], p["division"], p["lp"], p["rank_wins"], p["rank_lag_hours"]) == ("EMERALD", "I", 77, 10, 1.0)


# ---------------------------------------------------------------- writers


def test_tsv_values():
    assert tsv_value(None, "int") == ""
    assert tsv_value(True, "bool") == "true"
    assert tsv_value([1, 2, 3], "list_int") == "1,2,3"
    assert tsv_value("a\tb\nc\rd", "str") == "a b c d"
    assert tsv_value(0, "ts") == "1970-01-01T00:00:00.000Z"
    assert tsv_value(1.5, "float") == "1.5"


def test_tsv_escaping_round_trip(tmp_path):
    table = Table("t", (Column("name", "str"), Column("n", "int"), Column("ok", "bool")), partitioned=False)
    writer = TsvWriter(tmp_path, table)
    writer.write({"name": "Tab\there\nnew line", "n": 5, "ok": False})
    writer.write({"name": None, "n": None, "ok": True})
    writer.close()
    with open(tmp_path / "part-0001.tsv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    assert rows == [["name", "n", "ok"], ["Tab here new line", "5", "false"], ["", "", "true"]]


def test_tsv_rolls(tmp_path):
    table = Table("t", (Column("v", "str"),), partitioned=False)
    writer = TsvWriter(tmp_path, table, roll_bytes=50)
    for i in range(10):
        writer.write({"v": f"value-{i:03d}"})
    writer.close()
    parts = sorted(tmp_path.glob("part-*.tsv"))
    assert len(parts) > 1
    assert all(p.read_text().startswith("v\n") for p in parts)
    assert sum(len(p.read_text().splitlines()) - 1 for p in parts) == 10


# ---------------------------------------------------------------- end-to-end export


@pytest.fixture
def data_dir(tmp_path):
    raw = tmp_path / "raw"
    with RawWriter(raw, "matches", fsync=False) as w:
        w.write("EUN1", match_rec())
        w.write("EUN1", match_rec())  # duplicate line after a crash
        second = json.loads(json.dumps(MATCH))
        second["metadata"]["matchId"] = "KR_1"
        w.write("KR", match_rec(second, "KR_1", "KR"))
    with RawWriter(raw, "timelines", fsync=False) as w:
        w.write("EUN1", timeline_record(MATCH_ID, "EUN1", TIMELINE))
        w.write("EUN1", timeline_record("EUN1_orphan", "EUN1", TIMELINE))  # no match -> skipped
    with RawWriter(raw, "ranks", fsync=False) as w:
        record = rank_record(PUUID, "EUN1", "RANKED", ranked(), fetched_at=iso(GAME_END + HOUR))
        w.write("EUN1", record)
        w.write("EUN1", record)  # duplicate
    ddragon = raw / "ddragon" / "16.20.1"
    ddragon.mkdir(parents=True)
    (ddragon / "champion.json").write_text(json.dumps({"data": {"Yone": {"key": "777", "id": "Yone", "name": "Yone",
        "title": "the Unforgotten", "tags": ["Assassin", "Fighter"], "partype": "None",
        "info": {"attack": 8, "defense": 4, "magic": 4, "difficulty": 8}, "stats": {"hp": 620, "attackspeed": 0.625}}}}))
    (ddragon / "item.json").write_text(json.dumps({"data": {"1001": {"name": "Boots", "gold": {"base": 300, "total": 300,
        "sell": 210, "purchasable": True}, "tags": ["Boots"], "into": ["3006"], "maps": {"11": True}, "stats": {"FlatMovementSpeedMod": 25}}}}))
    (ddragon / "summoner.json").write_text(json.dumps({"data": {"SummonerFlash": {"key": "4", "id": "SummonerFlash",
        "name": "Flash", "cooldown": [300], "summonerLevel": 7, "modes": ["CLASSIC"]}}}))
    (ddragon / "runesReforged.json").write_text(json.dumps([{"id": 8000, "key": "Precision", "name": "Precision",
        "slots": [{"runes": [{"id": 8005, "key": "PressTheAttack", "name": "Press the Attack"}]}]}]))
    return tmp_path


def test_export_both_formats(data_dir):
    summary = export(data_dir)
    assert summary.matches == 2 and summary.duplicate_matches == 1 and summary.duplicate_ranks == 1
    assert summary.timelines == 1 and summary.matches_without_timeline == 1
    assert summary.rows[("matches", "EUN1")] == 1 and summary.rows[("participants", "KR")] == 10
    assert summary.rows[("ranks", "EUN1")] == 1 and summary.rows[("dim_champions", None)] == 1
    assert summary.rank_status["RANKED"] == 2  # the same player in both matches
    assert summary.patches_without_ddragon == []

    out = data_dir / "export"
    participants = pq.read_table(out / "parquet" / "participants")
    assert participants.num_rows == 20
    assert set(participants.column("platform_id").to_pylist()) == {"EUN1", "KR"}  # from the folder name
    assert participants.schema.field("kills").type == pa.int64()
    assert participants.schema.field("win").type == pa.bool_()
    assert participants.schema.field("rank_fetched_at").type == pa.timestamp("ms", tz="UTC")
    single = pq.read_table(out / "parquet" / "participants" / "platform_id=EUN1" / "part-0001.parquet")
    assert "platform_id" not in single.schema.names
    teams = pq.read_table(out / "parquet" / "teams")
    assert teams.schema.field("bans").type == pa.list_(pa.int64())
    frames = pq.read_table(out / "parquet" / "timeline_participant_frames")
    assert frames.num_rows == len(TIMELINE["info"]["frames"]) * 10
    champions = pq.read_table(out / "parquet" / "dim_champions").to_pylist()
    assert champions[0]["champion_id"] == 777 and champions[0]["tags"] == ["Assassin", "Fighter"]

    with open(out / "tsv" / "matches" / "platform_id=EUN1" / "part-0001.tsv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE))
    assert len(rows) == 1 and rows[0]["match_id"] == MATCH_ID and rows[0]["platform_id"] == "EUN1"
    assert rows[0]["game_end"].endswith("Z")
    with open(out / "tsv" / "teams" / "platform_id=EUN1" / "part-0001.tsv", encoding="utf-8") as f:
        header, first = f.readline().rstrip("\n").split("\t"), f.readline().rstrip("\n").split("\t")
    assert len(first) == len(header) == len(TABLES["teams"].columns)
    assert first[header.index("bans")].count(",") == 4


def test_export_tsv_only_writes_no_parquet(data_dir):
    export(data_dir, formats=("tsv",))
    assert (data_dir / "export" / "tsv" / "matches").is_dir()
    assert not (data_dir / "export" / "parquet").exists()


def test_export_is_rerunnable_and_selective(data_dir):
    export(data_dir)
    stale = data_dir / "export" / "parquet" / "matches" / "platform_id=EUN1" / "stale.parquet"
    stale.write_bytes(b"old")
    keep = data_dir / "export" / "parquet" / "teams" / "platform_id=EUN1" / "part-0001.parquet"
    export(data_dir, formats=("parquet",), tables=("matches",))
    assert not stale.exists()  # selected table regenerated from scratch
    assert keep.exists()  # other tables untouched


def test_export_platform_filter(data_dir):
    summary = export(data_dir, tables=("matches",), platforms=("kr",))
    assert dict(summary.rows) == {("matches", "KR"): 1}


def test_missing_ddragon_is_reported(data_dir):
    import shutil

    shutil.rmtree(data_dir / "raw" / "ddragon")
    summary = export(data_dir, tables=("matches",))
    assert summary.patches_without_ddragon == ["16.20"]


def test_unknown_table_rejected(data_dir):
    with pytest.raises(ValueError):
        export(data_dir, tables=("players",))
