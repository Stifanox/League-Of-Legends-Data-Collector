# Phase 5 — Export (TSV + Parquet)

You are implementing phase 5 of the Riot API crawler rewrite. Phases 0–4 are done: the crawler writes raw `jsonl.gz` files under `data/raw/{matches,timelines,ranks}/platform=XXX/` and Data Dragon JSON under `data/raw/ddragon/<version>/`.

## Read first
- `docs/DESIGN.md` §10 (raw layout + envelopes) and **§11 (export — tables, formats, derived values)**, §13.
- `tests/fixtures/` (real match, timeline, league responses) and `crawler/raw_writer.py`.

## Rules
- **No git write actions.**
- Export makes **zero** API calls (Data Dragon files: use what is on disk; if a needed version is missing, log a warning and leave name columns empty — `python -m crawler ddragon --patch X.Y` fetches it).
- Constant memory: stream raw files line by line; Parquet written in row batches (~50k rows). Must work on 8 GB RAM for ~100k matches with timelines.
- Re-runnable: wipe and regenerate `data/export/` (only the selected formats/tables).

## Tasks
1. `crawler/export/schemas.py` — one schema per table (ordered column names + pyarrow types), the single source for both writers. Tables per DESIGN §11: `matches`, `participants`, `teams`, `timeline_participant_frames`, `timeline_events`, `ranks`, `dim_champions`, `dim_items`, `dim_summoner_spells`, `dim_runes`. Use the real fixture JSON to choose fields; keep the column set broad (the ML topic is unknown) but flat.
2. `crawler/export/flatten.py` — pure functions raw record → rows per table. Include: `patch` from `gameVersion`; `platform_id` on every row; `seed_tier`/`strategy` on matches; participant rank join + `rank_status` + `rank_lag_hours` per DESIGN §11 (build an in-memory index `puuid → sorted fetched_at list` from the `ranks` raw files first — it is small); timeline frames per minute × participant; events with all relevant optional fields (missing → null).
3. `crawler/export/writers.py`:
   - `TsvWriter`: header row, tab-separated, `\t`/`\r`/`\n` in strings replaced by space, lists comma-joined, null → empty, bool → `true/false`; output `data/export/tsv/<table>/platform_id=XXX/part-NNNN.tsv`, rolled at ~128 MB;
   - `ParquetWriter`: `pyarrow.parquet.ParquetWriter` per (table, platform), snappy or zstd compression, row batches; `data/export/parquet/<table>/platform_id=XXX/part-NNNN.parquet`. Note: since `platform_id` is a Hive partition folder, keep it as a column in TSV, and decide (document it) whether to also keep it inside Parquet files — Spark handles both, pandas reading a single file benefits from keeping it.
4. `crawler/export/run.py` + CLI: `python -m crawler export [--formats tsv,parquet] [--tables a,b] [--platforms EUN1,...]`; dedupe by `match_id` (and `(puuid, fetched_at)` for ranks); timelines only for matches present; summary at the end (rows per table per platform, matches without timeline, rank_status counts, rank_lag percentiles).
5. Tests:
   - flatten the fixture match → 1 `matches` row, 10 `participants`, 2 `teams`; fixture timeline → frames count = minutes × 10; events typed;
   - `patch` derivation; rank join cases (lookup after game, only before game, none → `NOT_FETCHED`, `UNRANKED`);
   - TSV escaping (tab/newline in a name), round-trip read with `csv` module; Parquet round-trip with `pyarrow` types intact;
   - duplicate raw line exported once; `--formats tsv` writes no Parquet.

## Acceptance
- `.venv/bin/pytest -q` passes.
- Smoke: on data from phase 4, `python -m crawler export`; then
  - `python -c "import pandas as pd; print(pd.read_parquet('data/export/parquet/participants').groupby('tier').size())"` (install pandas only in your shell if missing — do not add it to requirements unless the user agrees),
  - `head -3 data/export/tsv/matches/platform_id=EUN1/part-0001.tsv`,
  - report sizes of raw vs TSV vs Parquet.

## Hand-over
Reply with: files, final column lists per table, test summary, smoke output, size comparison. Then stop.
