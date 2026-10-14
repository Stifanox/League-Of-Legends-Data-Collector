# Phase 4 — The crawler

You are implementing phase 4 of the Riot API crawler rewrite. Phases 0–3 are done: config, logging, HTTP core (limiter, errors, key pause, offline mode), endpoint wrappers, Data Dragon, SQLite state, raw writer, shutdown and disk guard.

## Read first
- `docs/DESIGN.md` §7 (error policy, parking), §8 (crawl strategy — the core of this phase), §9, §12, §13.
- All of `riot_client/` and `crawler/`.

## Rules
- **No git write actions.**
- Matches must never wait for rank lookups.
- Everything must be resumable: killing the process at any moment and restarting continues without losing or duplicating data (beyond the single possible duplicate line documented in DESIGN §9).

## Tasks
1. `crawler/seeding.py` — **stratified** strategy per region (DESIGN §8):
   - next tier = least-collected non-exhausted tier;
   - non-apex: random division + random page; empty page handling; pick a few random unseeded players;
   - apex: league endpoint; once all seeded, revisit with deeper paging (`start += K`); mark exhausted when nothing new in-window;
   - returns seed players with `seed_tier`.
   - **snowball** strategy: `SEED_RIOT_ID` → account-v1 → PUUID; next player = random unvisited participant from collected matches of that region.
2. `crawler/workers.py`:
   - one **match worker** per region: get seed player → `match_ids_by_puuid` (queue 420, window, `start`, `count=K`) → skip seen → fetch match → persist → fetch timeline if `FETCH_TIMELINES` → persist → update tier counters → enqueue the 10 participants for rank lookup;
   - one **rank worker** per region (platform host): consume rank queue; skip if looked up within `RANK_REUSE_HOURS`; `entries_by_puuid` → `solo_queue_rank` → persist rank raw record + state;
   - error handling per DESIGN §7: `NotFound`/`BadRequest` → log + skip; exhausted retries / `InvalidResponse` → park; `KeyInvalid` / offline are handled inside the HTTP client (workers simply await);
   - `retry-parked` pass at the end of each scheduling cycle (bounded), and as a CLI command.
3. `crawler/run.py` — orchestrates: load settings, warn if window crosses `SPLIT_RESET_DATE`, start client, workers for each region in `REGIONS`, disk guard (pause below `MIN_FREE_DISK_GB`, check every few minutes), shutdown controller, optional `--max-matches N` and `--duration 48h` stop conditions.
4. Progress/status UI (`crawler/progress.py`): progress bar of total matches + compact status per region: req/s on regional and platform host, matches collected, rank backlog, per-tier `collected/exhausted`, state flags `PAUSED: key` / `OFFLINE` / `LOW DISK`. Refresh ~1 s. Must not break when stdout is not a TTY (fall back to periodic log lines).
5. CLI (`crawler/__main__.py`): `crawl [--max-matches N] [--duration 48h] [--regions ...]`, `retry-parked`, `status` (counts per region/tier, parked by kind, last key error, disk free).
6. Tests (MockTransport + fixtures + temp dirs, fake clock where useful):
   - stratified selection balances tiers and redistributes when one is exhausted;
   - apex fallback deepens paging then exhausts;
   - dedupe: a match seen via two players is fetched once;
   - rank reuse window; unranked → `UNRANKED`; failed lookup → parked, not blocking matches;
   - `FETCH_TIMELINES=false` skips timelines;
   - crash/resume: run, stop mid-way (cancel), restart → continues, no duplicate fetches of done matches;
   - 401 mid-run pauses all region workers and resumes after key change.

## Acceptance
- `.venv/bin/pytest -q` passes.
- Smoke 1 (dev key): `python -m crawler crawl --regions eun1 --max-matches 20` → 20 matches + timelines, ranks for participants, `status` shows tier distribution.
- Smoke 2: `python -m crawler crawl --max-matches 30` (all 3 regions) — observe the three regional hosts working in parallel; press Ctrl-C mid-way; restart; verify it continues.
- Smoke 3: put a wrong key in `.env` during a run → paused; fix → press Enter → resumes.

## Hand-over
Reply with: files, test summary, smoke observations (requests/s per host, matches/min), any limit/throughput surprises. Then stop.
