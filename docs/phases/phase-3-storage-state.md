# Phase 3 — Storage and state (SQLite, raw writer, resume)

You are implementing phase 3 of the Riot API crawler rewrite. Phases 0–2 are done: config, logging, HTTP core, endpoint wrappers, Data Dragon.

## Read first
- `docs/DESIGN.md` §8 (what the crawler needs to remember), §9 (state + write order), §10 (storage layout, record envelopes), §13.

## Rules
- **No git write actions.**
- Pure standard library here (`sqlite3`, `gzip`, `json`). Storage code is synchronous; the crawler (phase 4) calls it from the event loop — keep each call short (no long transactions).

## Tasks
1. `crawler/state.py` — `StateStore(path)`:
   - SQLite, WAL mode, `synchronous=NORMAL`, schema version table + simple migration hook;
   - tables (names indicative): `players` (puuid, platform_id, tier, source, visited_at, next_start, exhausted), `matches` (match_id PK, platform_id, status `queued|done|parked`, match_done, timeline_done, seed_tier, seed_puuid, strategy, updated_at), `tier_progress` (platform_id, tier, collected, exhausted, apex_round), `rank_lookups` (puuid, platform_id, fetched_at, status), `parked` (kind `match|timeline|rank|ids|league`, key, platform_id, error, status_code, attempts, last_attempt), `meta` (key, value);
   - methods the crawler needs: enqueue/claim/mark-done/park for matches; add/claim seed players with paging offset; tier counters + exhausted flags + "least-collected non-exhausted tier"; `rank_recent(puuid, within_hours)`; record rank lookup; parked list/retry; `is_seen(match_id)`; summary for `status`.
2. `crawler/raw_writer.py` — `RawWriter(root, kind, roll_mb)`:
   - writes to `data/raw/<kind>/platform=<PLATFORM_ID>/part-NNNN.jsonl.gz` (platform folder uppercase, e.g. `EUN1`);
   - one JSON object per line, `ensure_ascii=False`, compact separators;
   - **flush after every record** (gzip member flush so data survives a crash); roll to a new part when the compressed size reaches `roll_mb`; on startup continue numbering after existing parts (never append to an old part — start a new one);
   - envelope builders for match / timeline / rank records exactly as in DESIGN §10, `fetched_at` as ISO-8601 UTC.
3. Write order helper: `persist_match(...)`: raw append+flush → then mark done in state. Same for timeline and rank.
4. Graceful shutdown utility: `ShutdownController` installing SIGINT/SIGTERM handlers on the event loop, exposing an `asyncio.Event`; a second Ctrl-C forces exit. Closing writers flushes and finalises gzip streams.
5. Disk guard: `free_gb(path)` + `DiskGuard(min_gb)` that the crawler can poll.
6. Tests:
   - state: enqueue/claim/done/park round-trips; dedupe; least-collected-tier selection skips exhausted; rank reuse window; restart (new `StateStore` on the same file) sees prior state;
   - raw writer: records readable with `gzip.open(...,"rt")` line by line; rolling at tiny `roll_mb`; numbering continues after restart; a simulated crash (writer not closed) still leaves all flushed lines readable;
   - envelope shapes.

## Acceptance
- `.venv/bin/pytest -q` passes.
- Smoke: a small script/CLI path that uses phase-2 wrappers to fetch 3 matches (+ timelines, + one rank) for eun1, persists them, then `python -m crawler status` shows 3 done; `zcat data/raw/matches/platform=EUN1/part-0001.jsonl.gz | head -c 500` shows the envelope.

## Hand-over
Reply with: files, schema summary, test summary, smoke command. Then stop.
