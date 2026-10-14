# Phase 2 — Endpoint wrappers and Data Dragon

You are implementing phase 2 of the Riot API crawler rewrite. Phases 0–1 are done: config, logging, and `riot_client/http.py` (`RiotHttpClient.get_json` with rate limiting, error policy and key pause).

## Read first
- `docs/DESIGN.md` §5 (APIs and routing), §7 (404 / empty-list semantics), §10 (Data Dragon raw location), §13.
- `riot_client/http.py`, `riot_client/errors.py`, `crawler/config.py`.

## Rules
- **No git write actions.**
- Wrappers return **raw parsed JSON** (dicts/lists) — no lossy models. The raw data is the source of truth.
- `riot_client/` does not import `crawler/`.

## Tasks
1. `riot_client/routing.py` — platform → region map (DESIGN §5), host URL builders, tier/division constants (`IRON … CHALLENGER`, `I–IV`, apex set).
2. `riot_client/endpoints/match.py`:
   - `match_ids_by_puuid(region, puuid, *, queue, start_time, end_time, start, count)` (`count ≤ 100`);
   - `match(region, match_id)`; `timeline(region, match_id)`.
3. `riot_client/endpoints/league.py`:
   - `entries_by_puuid(platform, puuid)` → list (may be `[]`);
   - helper `solo_queue_rank(entries) -> {status: "RANKED"|"UNRANKED", tier, division, lp, wins, losses}`;
   - `entries_page(platform, tier, division, page)` for IRON…DIAMOND (queue `RANKED_SOLO_5x5`);
   - `apex_league(platform, tier)` for MASTER/GRANDMASTER/CHALLENGER;
   - a helper that returns the list of PUUIDs from either shape. **Verify against the live API** that entries contain `puuid`; if not, document what they contain and stop to ask.
4. `riot_client/endpoints/account.py` — `account_by_riot_id(region, game_name, tag_line)`; accept `"Name#TAG"` parsing helper.
5. Each wrapper passes a stable `method_key` to the limiter (e.g. `match-v5.ids`, `match-v5.match`, `match-v5.timeline`, `league-v4.by-puuid`, `league-v4.entries`, `league-v4.apex`, `account-v1.by-riot-id`).
6. `riot_client/ddragon.py` (plain `httpx`, no key, no limiter):
   - `versions()`; `resolve_version(patch "15.19") -> newest version with that major.minor`;
   - `download(version, dest_dir)` → `champion.json`, `item.json`, `summoner.json`, `runesReforged.json` (en_US), skip if already present.
   - wire `python -m crawler ddragon [--patch X.Y]` to download into `data/raw/ddragon/<version>/` (latest if no patch).
7. Fixtures: save **one real** match JSON, its timeline, one league entries page (trimmed to ~5 entries), one apex league (trimmed), one `entries_by_puuid` response (ranked) and an empty one, into `tests/fixtures/` — fetched once with a dev key. Strip nothing else; these drive phase 5 tests too. PUUIDs/names are fine to keep (public game data), but **never** a key.
8. Tests (MockTransport serving fixtures): correct host per platform/region, correct path and query params, `method_key` used, `solo_queue_rank` for ranked / Flex-only / `[]`, PUUID extraction from both league shapes, `resolve_version`.

## Acceptance
- `.venv/bin/pytest -q` passes.
- Smoke (dev key), e.g. `python -m crawler status --probe eun1`: fetch one CHALLENGER league for eun1, take 1 player, fetch 1 match ID (queue 420, last 14 days), fetch match + timeline, fetch that player's rank, print sizes and the rank. Also `python -m crawler ddragon`.

## Hand-over
Reply with: files, test summary, smoke output summary, any API shape differences from DESIGN §5. Then stop.
