"""`python -m crawler export`: raw `jsonl.gz` -> TSV / Parquet tables. No API calls (DESIGN §11).

Passes, each streaming raw files line by line:
1. ranks      -> `ranks` table + an in-memory index used to join ranks onto participants;
2. matches    -> `matches`, `participants`, `teams` (deduped by match id);
3. timelines  -> `timeline_participant_frames`, `timeline_events` (only for exported matches);
4. Data Dragon versions on disk -> `dim_*` tables.
The selected tables/formats (and platforms) are wiped and fully regenerated.
"""

from __future__ import annotations

import json
import logging
import shutil
import statistics
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from crawler.export.flatten import RankIndex, flatten_ddragon, flatten_match, flatten_rank, flatten_timeline
from crawler.export.schemas import TABLES, Table
from crawler.export.writers import FORMATS, ExportWriters, table_dir
from crawler.raw_writer import list_parts, read_records
from riot_client.ddragon import FILES as DDRAGON_FILES, patch_of, version_key

log = logging.getLogger(__name__)

MATCH_TABLES = ("matches", "participants", "teams")
TIMELINE_TABLES = ("timeline_participant_frames", "timeline_events")
DIM_TABLES = ("dim_champions", "dim_items", "dim_summoner_spells", "dim_runes")
LOG_EVERY = 1000


@dataclass
class ExportSummary:
    rows: Counter = field(default_factory=Counter)  # (table, platform_id | None) -> rows
    matches: int = 0
    duplicate_matches: int = 0
    timelines: int = 0
    matches_without_timeline: int | None = None
    duplicate_ranks: int = 0
    rank_status: Counter = field(default_factory=Counter)
    rank_lag_hours: list[float] = field(default_factory=list)
    patches: Counter = field(default_factory=Counter)
    patches_without_ddragon: list[str] = field(default_factory=list)

    def lag_percentiles(self) -> dict[str, float] | None:
        lags = self.rank_lag_hours
        if len(lags) < 2:
            return None
        q = statistics.quantiles(lags, n=100, method="inclusive")
        return {"p50": q[49], "p90": q[89], "p99": q[98], "max": max(lags)}


def export(
    data_dir: Path,
    *,
    formats: tuple[str, ...] = FORMATS,
    tables: tuple[str, ...] | None = None,
    platforms: tuple[str, ...] | None = None,
    **writer_options,
) -> ExportSummary:
    """Regenerate `data_dir/export/<format>/<table>/...` from `data_dir/raw`."""
    tables = tuple(tables or TABLES)
    unknown = set(tables) - set(TABLES)
    if unknown:
        raise ValueError(f"Unknown table(s) {sorted(unknown)}; known: {', '.join(TABLES)}")
    raw = data_dir / "raw"
    out = data_dir / "export"
    selected_platforms = _platforms(raw, platforms)
    _wipe(out, formats, [TABLES[t] for t in tables], platforms)

    summary = ExportSummary()
    writers = ExportWriters(out, formats, **writer_options)
    try:
        ranks = _export_ranks(raw, selected_platforms, tables, writers, summary)
        patches = _export_matches(raw, selected_platforms, tables, ranks, writers, summary)
        if set(tables) & set(TIMELINE_TABLES):
            _export_timelines(raw, selected_platforms, tables, patches, writers, summary)
        _export_ddragon(raw, tables, writers, summary)
    finally:
        writers.close()
    summary.rows = writers.row_counts
    return summary


# ----------------------------------------------------------------- passes


def _export_ranks(raw: Path, platforms: list[str], tables, writers: ExportWriters, summary: ExportSummary) -> RankIndex | None:
    if "ranks" not in tables and "participants" not in tables:
        return None
    index = RankIndex()
    seen: set[tuple[str, int]] = set()
    for platform_id in platforms:
        for record in _records(raw, "ranks", platform_id):
            row = flatten_rank(record)
            key = (row["puuid"], row["fetched_at"])
            if key in seen:
                summary.duplicate_ranks += 1
                continue
            seen.add(key)
            index.add(row)
            if "ranks" in tables:
                writers.write(TABLES["ranks"], platform_id, row)
    log.info("ranks: %d lookups indexed", len(index))
    return index


def _export_matches(
    raw: Path, platforms: list[str], tables, ranks: RankIndex | None, writers: ExportWriters, summary: ExportSummary
) -> dict[str, str | None]:
    """Returns `match_id -> patch` of every exported match (used by the timeline pass)."""
    patches: dict[str, str | None] = {}
    wanted = [t for t in MATCH_TABLES if t in tables]
    for platform_id in platforms:
        for record in _records(raw, "matches", platform_id):
            match_id = record.get("match_id")
            if not match_id or match_id in patches:
                summary.duplicate_matches += bool(match_id)
                continue
            rows = flatten_match(record, ranks)
            patch = rows["matches"][0]["patch"]
            patches[match_id] = patch
            summary.patches[patch] += 1
            summary.matches += 1
            for participant in rows["participants"]:
                summary.rank_status[participant["rank_status"]] += 1
                if participant["rank_lag_hours"] is not None:
                    summary.rank_lag_hours.append(participant["rank_lag_hours"])
            for table in wanted:
                for row in rows[table]:
                    writers.write(TABLES[table], platform_id, row)
            if summary.matches % LOG_EVERY == 0:
                log.info("matches: %d exported", summary.matches)
    return patches


def _export_timelines(
    raw: Path, platforms: list[str], tables, patches: dict[str, str | None], writers: ExportWriters, summary: ExportSummary
) -> None:
    done: set[str] = set()
    wanted = [t for t in TIMELINE_TABLES if t in tables]
    for platform_id in platforms:
        for record in _records(raw, "timelines", platform_id):
            match_id = record.get("match_id")
            if match_id not in patches or match_id in done:
                continue
            done.add(match_id)
            rows = flatten_timeline(record, patches[match_id])
            for table in wanted:
                for row in rows[table]:
                    writers.write(TABLES[table], platform_id, row)
            if len(done) % LOG_EVERY == 0:
                log.info("timelines: %d exported", len(done))
    summary.timelines = len(done)
    summary.matches_without_timeline = len(patches) - len(done)


def _export_ddragon(raw: Path, tables, writers: ExportWriters, summary: ExportSummary) -> None:
    versions = _ddragon_versions(raw / "ddragon")
    covered = {patch_of(v) for v in versions}
    summary.patches_without_ddragon = sorted(p for p in summary.patches if p and p not in covered)
    for patch in summary.patches_without_ddragon:
        log.warning("No Data Dragon files for patch %s - run: python -m crawler ddragon --patch %s", patch, patch)
    wanted = [t for t in DIM_TABLES if t in tables]
    if not wanted:
        return
    for version in versions:
        directory = raw / "ddragon" / version
        files = {}
        for name in DDRAGON_FILES:
            path = directory / name
            if path.exists():
                files[name] = json.loads(path.read_text(encoding="utf-8"))
        rows = flatten_ddragon(version, files)
        for table in wanted:
            for row in rows[table]:
                writers.write(TABLES[table], None, row)


# ----------------------------------------------------------------- helpers


def _platforms(raw: Path, only: tuple[str, ...] | None) -> list[str]:
    found = set()
    for kind in ("matches", "timelines", "ranks"):
        directory = raw / kind
        if directory.is_dir():
            found |= {p.name.split("=", 1)[1] for p in directory.iterdir() if p.is_dir() and p.name.startswith("platform=")}
    if only is not None:
        found &= {p.upper() for p in only}
    return sorted(found)


def _records(raw: Path, kind: str, platform_id: str) -> Iterator[dict]:
    for part in list_parts(raw / kind / f"platform={platform_id}"):
        yield from read_records(part)


def _ddragon_versions(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    versions = [p.name for p in directory.iterdir() if p.is_dir() and version_key(p.name) is not None]
    return sorted(versions, key=version_key)


def _wipe(out: Path, formats: tuple[str, ...], tables: list[Table], platforms: tuple[str, ...] | None) -> None:
    """Delete previous output of the selected formats/tables (only the selected platforms, if given)."""
    for fmt in formats:
        for table in tables:
            if table.partitioned and platforms:
                targets = [table_dir(out, fmt, table, p.upper()) for p in platforms]
            else:
                targets = [out / fmt / table.name]
            for target in targets:
                if target.exists():
                    shutil.rmtree(target)
