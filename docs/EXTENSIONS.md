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
- **Other queues** — Flex (440), ARAM (450), Arena; normals for behavioural comparison.
- **Continuous mode** — scheduled daily runs (systemd timer) appending new matches, with per-patch partitions.
- **Pro / high-elo focus** — apex-only crawler with deep history.
- **Live game snapshots** via spectator-v5 (pre-game features: who is playing what, before the result is known).

## 3. Dataset tooling

- `systemd --user` unit with auto-restart and `loginctl enable-linger` for unattended multi-day runs.
- Splittable compressed text export (bzip2 / indexed LZO) for very large MapReduce inputs.
- Delta Lake / Iceberg tables if the dataset becomes long-lived and incrementally updated.
- Feature-engineering exports for specific ML tasks once the topic is chosen (e.g. per-minute team-diff tensors as NPY/Arrow, train/val/test splits by match date to avoid leakage).
- Data-quality report (counts per region/tier/patch, missing ranks, rank lag distribution) as part of `export`.
