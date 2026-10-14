"""End to end: crawl two regions against the fake API, then export both formats."""

from __future__ import annotations

import csv
import json
import random

import httpx
import pyarrow.parquet as pq

from crawler.config import load_settings
from crawler.export.run import export
from crawler.run import crawl
from tests.conftest import FIXTURES_DIR
from tests.fake_riot import FakeRiot
from tests.virtual_time import run

KEY = "RGAPI-11111111-aaaa-bbbb-cccc-000000000001"
FRAMES = len(json.loads((FIXTURES_DIR / "timeline.json").read_text(encoding="utf-8"))["info"]["frames"])


def test_crawl_two_regions_then_export(write_env, tmp_path):
    env = write_env(
        RIOT_API_KEY=KEY,
        REGIONS="eun1,kr",
        DATA_DIR=str(tmp_path / "data"),
        LOG_DIR=str(tmp_path / "logs"),
        MIN_FREE_DISK_GB="0",
        MATCHES_PER_PLAYER="5",
    )
    settings = load_settings(env)
    fake = FakeRiot(KEY, platforms=("EUN1", "KR"), players_per_tier={"CHALLENGER": 1}, matches_per_player=3, real_timelines=True)

    ctx = run(
        crawl(
            settings,
            env,
            show_progress=False,
            install_signals=False,
            transport=httpx.MockTransport(fake),
            notify=False,
            rng=random.Random(1),
        )
    )
    assert {pid: s.matches for pid, s in ctx.stats.items()} == {"EUN1": 3, "KR": 3}

    summary = export(settings.data_dir)
    out = settings.data_dir / "export"
    expected = {
        "matches": 3,
        "participants": 30,
        "teams": 6,
        "timeline_participant_frames": 3 * FRAMES * 10,
        "ranks": 1 + 3 * 9,  # the seed player is in all 3 matches but looked up once (reuse window)
    }
    for platform_id in ("EUN1", "KR"):
        for table, rows in expected.items():
            assert summary.rows[(table, platform_id)] == rows, (table, platform_id)

            parquet = pq.read_table(out / "parquet" / table / f"platform_id={platform_id}")
            assert parquet.num_rows == rows
            tsv_rows = 0
            for part in sorted((out / "tsv" / table / f"platform_id={platform_id}").glob("part-*.tsv")):
                with open(part, newline="", encoding="utf-8") as f:
                    tsv_rows += sum(1 for _ in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)) - 1
            assert tsv_rows == rows

    all_participants = pq.read_table(out / "parquet" / "participants")
    assert set(all_participants.column("platform_id").to_pylist()) == {"EUN1", "KR"}
    assert set(all_participants.column("rank_status").to_pylist()) == {"RANKED"}
    assert summary.matches_without_timeline == 0
