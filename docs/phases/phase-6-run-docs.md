# Phase 6 — Run script, README, end-to-end check

You are implementing phase 6 (final) of the Riot API crawler rewrite. Phases 0–5 are done: crawler and export work.

## Read first
- `docs/DESIGN.md` §2, §11, §12, and the whole doc for the README content.
- The CLI in `crawler/__main__.py`.

## Rules
- **No git write actions.**
- No systemd unit and no tmux docs (explicitly out of scope; see `docs/EXTENSIONS.md`).

## Tasks
1. `run.sh` (executable, `#!/usr/bin/env bash`, `set -euo pipefail`):
   - `cd` to the script's directory, require `.venv` and `.env` (clear message if missing);
   - `exec systemd-inhibit --what=handle-lid-switch:sleep:idle --who="lol-crawler" --why="Collecting Riot API data" .venv/bin/python -m crawler crawl "$@"`;
   - if `systemd-inhibit` is not available, warn and run without it.
2. `README.md` (replace any old one), concise:
   - what the project collects and why (Big Data + ML), link to `docs/DESIGN.md`;
   - setup: Python 3.13 venv, `pip install -r requirements.txt`, copy `.env.example` → `.env`, get a dev key;
   - **48 h run checklist**: free disk (~30 GB raw + exports), set lid behaviour (systemd-inhibit via `run.sh`; if the desktop still suspends, KDE/GNOME "lid closed → do nothing"), power adapter connected, network stable, `./run.sh --duration 48h`;
   - **key swap**: dev key expires every 24 h → edit `.env` (locally or SSH); crawler resumes within 30 s, or press Enter in its terminal;
   - commands: `crawl`, `status`, `retry-parked`, `ddragon`, `export --formats ...`;
   - data layout (raw / export) and how to read it: Spark (`spark.read.parquet("data/export/parquet/participants")`, TSV with `sep="\t"`), Hadoop Streaming example mapper splitting on `\t`, pandas example;
   - known limitations: rank is the current rank at lookup (`rank_lag_hours`), apex tiers smaller, one duplicate line possible after crash (export dedupes).
3. End-to-end test (`tests/test_e2e.py`, MockTransport + fixtures, temp data dir): crawl a few fake matches across 2 regions → export both formats → assert tables exist with expected row counts.
4. Final sweep: run the whole test suite, `python -m crawler --help`, check `.env`/`data/`/`logs/` are ignored, grep the repo for `RGAPI-` and for `api_key=` (must find nothing but the masking regex/docs).

## Acceptance
- `.venv/bin/pytest -q` passes.
- Smoke: `./run.sh --max-matches 10` then `python -m crawler export`; close the laptop lid for a minute during the crawl and confirm it kept running (`status` count increased / log timestamps continuous).

## Hand-over
Reply with: files, test summary, smoke result (including the lid test), and a short "ready for the 48 h run" checklist. Then stop.
