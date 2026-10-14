# Extensions — ideas for later

Everything here is **out of scope** for the current rewrite (see [`DESIGN.md`](DESIGN.md)). The crawler comes first; these are directions to pick up once the 48 h dataset exists.

## 1. `riot_client` as a standalone library

The rewrite already keeps `riot_client/` free of crawler logic. To turn it into a real library:
- move it to its own repo or a `packages/` folder with `pyproject.toml`, versioning, typed public API (`py.typed`);
- typed response models (dataclasses / pydantic / msgspec) for match, timeline, league entries;
- sync facade over the async client for scripts and notebooks;
- pluggable limiter backends (in-memory now; Redis/SQLite for several processes sharing one key);
- coverage of more endpoints (summoner-v4, champion-mastery-v4, challenges-v1, spectator-v5, clash, lol-status, account-v1 by-puuid);
- compare with existing libraries (`riotwatcher`, `pulsefire`, `cassiopeia`) before investing — decide whether to contribute instead of competing;
- optional: publish on PyPI.

## 2. Additional crawlers / data sources

- **Champion mastery crawler** — `champion-mastery-v4` per player (1 call per player); strong ML feature (player experience on the champion).
- **Rank tracker** — periodically re-query `league-v4` for a fixed set of players to build real rank/LP **history**, which the Riot API does not provide. Would fix the "rank at match time" limitation for tracked players.
- **More regions** — every additional regional cluster (`americas`, `asia`, `europe`, `sea`) adds a separate match-v5 bucket.
- **Other queues** — Flex (440), ARAM (450), Arena (1700); normals for behavioural comparison. `QUEUE_ID` covers part of it, but tier-stratified seeding only makes sense for ranked queues — the others need snowball seeding.
- **TFT** — `tft-match-v1` / `tft-league-v1` have nearly the same shape as match-v5 / league-v4: new endpoint wrappers, a TFT queue, and TFT-specific export flatteners; HTTP core, limiter, storage and state are reused as-is.
- **Valorant** — its APIs generally require an approved production key; not practical with a dev key.
- **Challenges** — `challenges-v1` per player (achievement-style statistics) as player-level features.
- **Continuous mode** — scheduled daily runs (systemd timer) appending new matches, with per-patch partitions.
- **Pro / high-elo focus** — apex-only crawler with deep history.
- **Live game snapshots** via spectator-v5 (pre-game features: who is playing what, before the result is known).

## 3. More crawl modes

The crawler has `CRAWL_MODE = full | matches-only | ranks-only` (DESIGN §8). Natural additions:
- **`timelines-only` (backfill)** — a timeline costs one regional request, the same as the match, so crawling with `FETCH_TIMELINES=false` yields ~2× more matches. A backfill mode would later fetch timelines for all (or a filtered subset, e.g. one tier or patch) of the collected matches. Mirrors `ranks-only`: queue = matches with `timeline_done = 0`.
- **Maximum rank lag** — e.g. `RANK_MAX_LAG_HOURS`: in `ranks-only`, skip queued lookups whose match ended too long ago, so the quota goes to ranks that still describe the player at game time.
- **Seeding priority on the platform host** — in `full` mode the rank worker keeps the platform limit busy and league-page seeding competes for the same slots; a priority in `HostLimiter.acquire` (seeding before ranks) would remove that delay. Measure first (compare `full` vs `matches-only` throughput).
- **Rank tracker mode** — see §2; re-query the same players on a schedule.

## 4. Reusing the crawler outside the Riot API

Most of the code is already API-agnostic: async HTTP client with retries/backoff, offline mode and credential pause + reload; crash-safe rolled `jsonl.gz` raw storage; SQLite work queue with parking/retries; graceful shutdown, disk guard, progress/status.

Riot-specific parts and how to make them pluggable:
- **Rate-limit header parser** — Riot sends `limit:seconds` pairs per app and per method without reset times. Most APIs send `X-RateLimit-Remaining` / `X-RateLimit-Reset` (simpler: the reset time is given); some have no headers and fixed documented limits.
- **Auth** — `X-Riot-Token` header → a small auth strategy (header, query param, OAuth bearer with refresh).
- **Source** — what Riot calls seeding + match ids + match/timeline becomes an interface: *discover items* (frontier), *fetch item*, *fetch detail*, *derive new seeds from an item*.
- **Export flatteners** — per source.

Candidate sources: OpenDota (Dota 2 matches — almost the same shape as this project), Steam Web API (fixed limits, no headers), GitHub (remaining/reset headers), football/sports data APIs.

## 5. Dataset tooling

- `systemd --user` unit with auto-restart and `loginctl enable-linger` for unattended multi-day runs.
- Splittable compressed text export (bzip2 / indexed LZO) for very large MapReduce inputs.
- Delta Lake / Iceberg tables if the dataset becomes long-lived and incrementally updated.
- Feature-engineering exports for specific ML tasks once the topic is chosen (e.g. per-minute team-diff tensors as NPY/Arrow, train/val/test splits by match date to avoid leakage).
- Data-quality report (counts per region/tier/patch, missing ranks, rank lag distribution) as part of `export`.
