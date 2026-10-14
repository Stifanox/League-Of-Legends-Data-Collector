# League of Legends Data Collector — Design (2026 rewrite)

Status: **agreed** (design session 2026-10-14). This document is the single source of truth for the rewrite.
Implementation is split into phases in [`phases/`](phases/); future ideas that are explicitly **out of scope** live in [`EXTENSIONS.md`](EXTENSIONS.md).

---

## 1. Goals

1. Build a dataset suitable for **Big Data processing** — Apache Hadoop (MapReduce) and Apache Spark.
2. Build a dataset suitable for **training ML models in Python** later. The ML topic is not known yet, so the raw data must be **lossless**.
3. Fix the problems of the 2022 version:
   - rate limiting was ad-hoc (fixed sleeps of 13 s / 122 s, recursive retries);
   - removed/deprecated endpoints (`summoner/v4/by-name`, `league/v4/entries/by-summoner`, summoner `id`/`accountId`/`name` fields);
   - API key in a tracked `constants/Config.py` and sent in the **query string** (leaks into URLs, logs, tracebacks);
   - poor error handling (non-200 bodies parsed as data, `IndexError` on empty batches, unbounded recursion between `__cycleAllCodes` ↔ `__chooseNewPlayer`);
   - dedupe file never loaded on Linux (Windows path `f"{ROOT}\match_codes.txt"`).

## 2. Runtime environment

- Old Linux laptop, **8 GB RAM**, ~**100 GB** free disk, **Python 3.13** (venv in `.venv`).
- One **48 h** unattended run, lid closed. The run must not be suspended (see §11).
- **Development API key** (expires every 24 h, limits `20 req / 1 s` and `100 req / 120 s` per routing value). The user swaps the key manually; a personal key may come later and must require **no code change**.

## 3. Rewrite rules

- Full rewrite in this repository; the old code is deleted (it remains in git history at `03a7b80`).
- Branch `rewrite/riot-api-2026`, created by the user. Implementation proceeds in phases 0–6.
- **Agents never run git write actions** (branch, commit, push, merge, stash, checkout). They edit files, run tests, and hand over for review. The user reviews and commits each phase.
- Code is split into two packages with a clean boundary:
  - `riot_client/` — HTTP, rate limiting, key handling, errors, endpoint wrappers, Data Dragon. No knowledge of crawling or storage.
  - `crawler/` — config, state, raw storage, crawl strategy, workers, CLI, export.
  - No extra effort on making `riot_client` a publishable library now (see EXTENSIONS.md).
- Dependencies (pinned in `requirements.txt`): `httpx`, `python-dotenv`, `pyarrow`, `alive-progress` (or `rich` if a multi-line status is clearly simpler — keep dependencies minimal), `pytest`, `pytest-asyncio`. Prefer the standard library otherwise (`sqlite3`, `gzip`, `csv`, `json`, `asyncio`, `logging`).

## 4. Configuration and secrets

- All config from environment / `.env` (loaded with `python-dotenv`). `.env` is **git-ignored**; `.env.example` is committed with every key documented and `RIOT_API_KEY=` empty.
- The key is sent **only** in the `X-Riot-Token` header — never in URLs.
- Logging has a filter that masks anything matching `RGAPI-[0-9a-fA-F-]+` as a safety net.
- `.gitignore` covers: `.env`, `data/`, `logs/`, `*.db`, `__pycache__/`, `*.pyc`, `.venv/`, `.pytest_cache/`. Committed `.pyc` files are removed.

| Variable | Default | Meaning |
|---|---|---|
| `RIOT_API_KEY` | — | Riot API key (required) |
| `REGIONS` | `eun1,na1,kr` | Platform routing values to crawl |
| `QUEUE_ID` | `420` | Ranked Solo/Duo |
| `WINDOW_DAYS` | `14` | Only matches started within the last N days |
| `SPLIT_RESET_DATE` | empty | ISO date of the last ranked reset; warn if the window starts before it |
| `FETCH_TIMELINES` | `true` | Fetch `/timeline` for every match |
| `MATCHES_PER_PLAYER` | `5` | K — match IDs taken per seed player per visit |
| `CRAWL_MODE` | `full` | `full`, `matches-only` or `ranks-only` (§8 Crawl modes); `crawl --mode` overrides it |
| `STRATEGY` | `stratified` | `stratified` or `snowball` |
| `SEED_RIOT_ID` | empty | `GameName#TAG`, start point for `snowball` |
| `RANK_REUSE_HOURS` | `6` | Reuse a player's rank lookup within this time |
| `RATE_LIMIT_SAFETY` | `0.9` | Use at most this fraction of every limit window |
| `KEY_POLL_SECONDS` | `30` | `.env` re-read interval while the key is invalid |
| `MAX_RETRIES` | `5` | Attempts for 5xx / network errors before parking |
| `OFFLINE_AFTER_SECONDS` | `300` | All requests failing this long → offline mode |
| `MIN_FREE_DISK_GB` | `2` | Pause crawling below this |
| `RAW_ROLL_MB` | `128` | Roll raw part files at roughly this compressed size |
| `DATA_DIR` | `data` | Root of raw/export/state |
| `LOG_DIR` | `logs` | Rotating log files |

## 5. Riot APIs used

Routing: **platform** hosts (`https://{platform}.api.riotgames.com`) and **regional** hosts (`https://{region}.api.riotgames.com`).
Mapping used: `eun1/euw1/tr1/ru/me1 → europe`, `na1/br1/la1/la2 → americas`, `kr/jp1 → asia`, `oc1/sg2/tw2/vn2 → sea` (match-v5).

| API | Endpoint | Host | Purpose |
|---|---|---|---|
| match-v5 | `/lol/match/v5/matches/by-puuid/{puuid}/ids?queue=420&startTime&endTime&start&count` | regional | match IDs of a player |
| match-v5 | `/lol/match/v5/matches/{matchId}` | regional | full match |
| match-v5 | `/lol/match/v5/matches/{matchId}/timeline` | regional | per-minute frames + events |
| league-v4 | `/lol/league/v4/entries/by-puuid/{puuid}` | platform | current rank of a player |
| league-v4 | `/lol/league/v4/entries/RANKED_SOLO_5x5/{tier}/{division}?page=N` | platform | seed players IRON…DIAMOND |
| league-v4 | `/lol/league/v4/{challenger,grandmaster,master}leagues/by-queue/RANKED_SOLO_5x5` | platform | seed players apex tiers |
| account-v1 | `/riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}` | regional | only for `snowball` manual seed |
| Data Dragon | `https://ddragon.leagueoflegends.com/api/versions.json`, `/cdn/{ver}/data/en_US/{champion,item,summoner,runesReforged}.json` | static CDN, no key, no rate limit | ID → name/stats dimensions, once per patch seen |

**Not used:** summoner-v4 (everything needed is in match participants and league-v4 by-puuid), champion-mastery-v4, challenges-v1, spectator-v5, clash, lol-status.

Implementers must verify exact response field names (e.g. that league entries contain `puuid`) against the live API with a dev key in the phase smoke test, and adapt if Riot changed something.

## 6. Rate limiting

Facts about Riot's headers:
- Every response: `X-App-Rate-Limit: 20:1,100:120`, `X-App-Rate-Limit-Count: 7:1,58:120`, `X-Method-Rate-Limit`, `X-Method-Rate-Limit-Count`. These give **limits and usage, but no reset time**.
- On 429: `Retry-After: <seconds>` and `X-Rate-Limit-Type: application|method|service`. For `service`, `Retry-After` is often **absent**.
- Limits are enforced **per routing value** (each platform host and each regional host is a separate bucket). Match calls for EUN1 and EUW1 share the `europe` bucket.

Design:
- One limiter **per host**; within it, one app-level window set plus one window set **per method** (endpoint template).
- Each window tracks its own start time and count client-side; `-Count` headers re-sync the count (take the max of local and server).
- Before the first response from a host, assume the dev-key app limits (`20:1,100:120`); update from headers afterwards.
- **Throttle before sending**: acquire waits until every applicable window has room under `limit × RATE_LIMIT_SAFETY`.
- On 429 with `Retry-After`: block that host (application) or that method (method) for exactly that long, then retry the same request. Without `Retry-After` (`service`): exponential backoff with jitter.
- On key change, reset all counters.

## 7. Error policy

| Situation | Action |
|---|---|
| 200 | Parse. Malformed/unexpected JSON → log with item id, **park** the item |
| 400 | Log, skip (not retryable) |
| 401 / 403 | Key invalid/expired → **pause the whole crawler**: log loudly, optionally `notify-send`, re-read `.env` every `KEY_POLL_SECONDS`; **Enter** on stdin (when it is a TTY) forces an immediate re-read. When a new key is found, validate it with a cheap call and resume. Nothing is marked failed. |
| 404 | Log, skip (match removed, bad id) |
| league-v4 by-puuid returns `[]` or no `RANKED_SOLO_5x5` entry | **Not an error** — result is `UNRANKED` |
| 429 | §6 |
| 500/502/503/504, timeouts, connection errors | Exponential backoff + jitter (1 s → 2 → 4 … cap 60 s), up to `MAX_RETRIES`, then **park** |
| All requests failing for `OFFLINE_AFTER_SECONDS` | **Offline mode**: probe every 30 s, resume when the network returns; retry counters not consumed while offline |
| Disk full / write error, or free space < `MIN_FREE_DISK_GB` | Pause (low space) or stop cleanly (write error) with a clear message; state stays consistent |

Parked items are stored in SQLite with error, status code and attempt count. They are retried at the end of each scheduling cycle and via `python -m crawler retry-parked`.

Logging: console shows a progress bar and a compact status line (per-region request rate, per-tier progress `collected/exhausted`, `PAUSED: key` / `OFFLINE` / `LOW DISK`). `logs/crawler.log` is a rotating file with full detail.

## 8. Crawl strategy

**Slice:** regions `eun1,na1,kr` (three different regional buckets ≈ 3× match throughput), queue 420 only, window `WINDOW_DAYS=14`. Warn at start if the window crosses `SPLIT_RESET_DATE`.

**Concurrency:** one process, `asyncio` + `httpx.AsyncClient`, one worker per region. Rank lookups use platform hosts and run concurrently with match fetching (separate buckets). Matches never wait for ranks.

**Default strategy — tier-stratified seeding (per region):**
1. Tiers: `IRON, BRONZE, SILVER, GOLD, PLATINUM, EMERALD, DIAMOND` (divisions `I–IV`) and `MASTER, GRANDMASTER, CHALLENGER`.
2. Next tier to work on = the **non-exhausted tier with the fewest collected matches** in that region (this equalises tiers and automatically redistributes the share of exhausted tiers).
3. Below apex: pick a random division and random page of `entries/RANKED_SOLO_5x5/{tier}/{division}`; pick a few random players not yet seeded. Apex: take players from the league endpoint.
4. For each seed player fetch the next `K = MATCHES_PER_PLAYER` match IDs (queue 420, inside the window); skip IDs already seen; fetch match (+ timeline).
5. Record `seed_tier`, `seed_puuid`, `platform_id` for every match.
6. **Apex fallback:** when every apex player of a tier has been seeded once, revisit them and page deeper (`start += K`) still inside the window. When no player yields new in-window matches, mark the tier **exhausted** for that region. A page of a non-apex tier that is empty also counts towards exhaustion for that division.

**Optional strategy — snowball:** `STRATEGY=snowball` + `SEED_RIOT_ID=Name#TAG` → account-v1 for the PUUID, then repeatedly pick an unvisited participant from collected matches. Same pipeline, same storage. `seed_tier` is empty.

**Crawl modes** (`CRAWL_MODE`, `crawl --mode`):

| Mode | Workers | Use |
|---|---|---|
| `full` | match + rank worker per platform, in parallel | default; ranks looked up soon after the game (small `rank_lag_hours`) |
| `matches-only` | match worker only; participants are still put in the rank queue | collect matches without rank lookups |
| `ranks-only` | rank worker only; no new players or matches | work off the saved rank queue later (larger lag) |

Throughput (dev key, measured in phase 4 smoke tests): a match needs ~2.3 regional requests (match, timeline, share of ids) → ~0.36 matches/s per region; ranks need ~10 platform requests per match but only ~0.8/s are available. In `full` mode roughly 20–25 % of participants get a rank; the queue is worked **newest first**, so ranks exist for the most recent matches with small lag and the rest stays queued for `ranks-only`. Match and rank calls use different hosts (regional vs platform), so `matches-only` collects about as many matches as `full`; seeding shares the platform host with ranks and may wait slightly longer in `full`.

**Ranks:**
- For each participant of every fetched match, look up `league-v4/entries/by-puuid` unless the same player was looked up within `RANK_REUSE_HOURS`.
- No API gives historical rank. We store the **current rank at lookup time** plus timing, and the export computes `rank_lag_hours`.
- `rank_status`: `RANKED` (Solo/Duo entry found), `UNRANKED` (API confirmed no Solo/Duo entry), `NOT_FETCHED` (lookup failed/parked/pending). The old `"UNSPECIFIED"` value is gone.

## 9. State and resumability

`data/state.db` (SQLite, WAL mode):
- seed-player queue and visited players (with paging offset for apex);
- match IDs: `queued | done | parked` (+ whether timeline is done);
- per region × tier counters and exhausted flags;
- rank lookup cache (`puuid`, `platform_id`, `fetched_at`, status);
- parked items (kind, key, error, status code, attempts, last attempt);
- key/run metadata.

Write order: append the raw record to the current part file and flush → mark done in SQLite. A crash between the two causes at most a duplicate raw line, which the export dedupes by `match_id`. SIGINT/SIGTERM: finish in-flight requests, flush, close files, exit. Restart resumes from state.

## 10. Storage layout

```
data/
  state.db
  raw/                                  # lossless source of truth, never modified by export
    matches/platform=EUN1/part-0001.jsonl.gz
    timelines/platform=EUN1/part-0001.jsonl.gz
    ranks/platform=EUN1/part-0001.jsonl.gz
    ddragon/<version>/{champion,item,summoner,runesReforged}.json
  export/                               # derived, fully regenerated by `export`
    tsv/<table>/platform_id=EUN1/part-0001.tsv
    parquet/<table>/platform_id=EUN1/part-0001.parquet
logs/crawler.log
```

- Raw files: gzip-compressed JSON Lines, one record per line. Rolled at ~`RAW_ROLL_MB` compressed (gzip is not splittable in Hadoop; ~128 MB parts give one mapper each).
- Raw record envelopes:
  - matches: `{"match_id", "platform_id", "fetched_at", "seed_tier", "seed_puuid", "strategy", "data": <match-v5 JSON>}`
  - timelines: `{"match_id", "platform_id", "fetched_at", "data": <timeline JSON>}`
  - ranks: `{"puuid", "platform_id", "fetched_at", "status", "entries": <league-v4 list>}`
- Every row of every file carries `platform_id`; files are additionally separated per platform (Hive-style folders, read by Spark as a column).

## 11. Export

`python -m crawler export [--formats tsv,parquet] [--tables ...]` — **no API calls**, re-runnable any time, overwrites `data/export/`. It streams raw files line by line (constant memory), dedupes by `match_id`, flattens into tables, and sends each row to every enabled writer in one pass. One schema (column name + type) per table drives both writers.

Formats (default both):
- **TSV** (plain, uncompressed, splittable) — for Hadoop MapReduce (`line.split("\t")`, Streaming default separator). Tabs and newlines in text values are replaced with spaces. Header row in each file. Lists are encoded as comma-joined strings.
- **Parquet** (via `pyarrow`, internally compressed, typed, columnar) — for Spark and pandas/polars/HF datasets.

Tables:

| Table | Grain | Columns (indicative) |
|---|---|---|
| `matches` | 1 / match | match_id, platform_id, patch, game_version, queue_id, game_start, game_end, duration_s, winning_team, seed_tier, strategy |
| `participants` | 10 / match | match_id, platform_id, patch, puuid, participant_id, team_id, win, champion_id, champion_name, team_position, kills, deaths, assists, gold_earned, total_minions_killed, neutral_minions_killed, damage dealt/taken breakdown, vision_score, wards placed/killed, control wards, items 0–6, summoner spells, primary/sub rune styles + perks, champ_level, riot_id_game_name, riot_id_tagline, summoner_level, rank_status, tier, division, lp, rank_wins, rank_losses, rank_lag_hours |
| `teams` | 2 / match | match_id, team_id, win, bans, baron/dragon/herald/horde/tower/inhibitor kills, first_* flags |
| `timeline_participant_frames` | 10 × minutes / match | match_id, minute, participant_id, puuid, current_gold, total_gold, xp, level, minions_killed, jungle_minions_killed, x, y, selected championStats/damageStats fields |
| `timeline_events` | many / match | match_id, timestamp_ms, minute, type, participant_id/killer_id/victim_id, assisting_ids, item_id, skill_slot, ward_type, building/monster type & subtype, team_id, x, y, bounty |
| `ranks` | 1 / lookup | puuid, platform_id, fetched_at, rank_status, tier, division, lp, wins, losses |
| `dim_champions`, `dim_items`, `dim_summoner_spells`, `dim_runes` | per patch | id, name, tags/cost/stats as available |

Derived values:
- `patch` = major.minor of `info.gameVersion` (e.g. `15.19.715.1234 → 15.19`).
- Rank for a participant of a match: the lookup for that `puuid` with the smallest `fetched_at ≥ gameEndTimestamp`; if none, the latest before it; none at all → `NOT_FETCHED`. `rank_lag_hours = (fetched_at − gameEnd) / 3600`.
- Data Dragon version for a patch = the newest DDragon version whose major.minor equals the patch.

## 12. Running

- CLI: `python -m crawler crawl | export | retry-parked | status | ddragon`.
- `run.sh`: activates `.venv`, runs `systemd-inhibit --what=handle-lid-switch:sleep:idle --why="LoL crawler" python -m crawler crawl "$@"`. This keeps the laptop awake with the lid closed **only while the crawler runs**. If the desktop environment's own power manager (e.g. KDE) still suspends on lid close, set "When lid closed → Do nothing" for the run.
- Key swap: edit `.env` (locally or via SSH). The crawler picks it up within `KEY_POLL_SECONDS`, or immediately on Enter.
- Budget estimate (dev key, 48 h, 3 regions, timelines on): roughly 100k+ matches, 15–30 GB raw. Crawler RAM well under 500 MB.

## 13. Verification

Every phase delivers:
- **pytest** tests that make **no live API calls**: fixtures with real-shaped match/timeline/league JSON in `tests/fixtures/`, and an `httpx.MockTransport` to simulate 200/400/401/403/404/429 (with and without `Retry-After`)/5xx/timeouts.
- One **manual smoke command** with a real dev key, stated in the phase prompt.
- A hand-over note for the user's review. **No git actions.**

## 14. Decision log (short)

| # | Decision |
|---|---|
| Q1–2 | Dev key, manual daily swap; on 401/403 pause, poll `.env`, Enter forces reload |
| Q3–4 | Full rewrite, branch `rewrite/riot-api-2026`, phased, user reviews/commits |
| Q5 | `riot_client` / `crawler` split; library/extra crawlers → EXTENSIONS.md |
| Q6–7 | Keep full raw JSON; timeline for every match (flag `FETCH_TIMELINES`) |
| Q8–10 | Multi-region `eun1,na1,kr`, Solo/Duo 420, per-platform folders + `platform_id` column, `patch` column |
| Q11 | APIs: match-v5, league-v4, account-v1 (seed only), Data Dragon; no summoner-v4 / mastery |
| Q12 | Tier-stratified seeding with apex fallback; optional snowball |
| Q13–14 | Rank of every participant = current rank at lookup; `rank_lag_hours`; window 14 days |
| Q15 | Export TSV + Parquet, `--formats` flag; raw is `jsonl.gz` |
| Q16 | Header-driven per-host limiter; asyncio; SQLite state; crash-safe writes |
| Q17 | Error policy §7; `rank_status` RANKED/UNRANKED/NOT_FETCHED |
| Q18 | `run.sh` + `systemd-inhibit`; no systemd unit / tmux |
| Q19 | pytest + smoke test per phase + user review |
| Q20 | Docs in `docs/`; phases 0–6 |
| Q21 | Crawl modes `full` / `matches-only` / `ranks-only` (`CRAWL_MODE`, `--mode`); rank queue newest first |
