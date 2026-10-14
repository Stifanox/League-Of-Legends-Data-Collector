# League of Legends Data Collector

Collects Ranked Solo/Duo matches from the Riot Games API, with per-minute timelines and player
ranks, and exports them as **TSV** and **Parquet**.
The raw API responses are kept losslessly, so new tables can be derived later.

## Setup

```bash
python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # then put your key into RIOT_API_KEY
.venv/bin/python -m crawler status --ping    # checks the key and shows the rate limits
```

Get a development key at <https://developer.riotgames.com> (log in → *Regenerate API Key*).
The key is only ever sent in the `X-Riot-Token` header and masked in logs.
Every setting is documented in `.env.example`; the most important ones:

| Setting | Default | Meaning |
|---|---|---|
| `REGIONS` | `eun1,na1,kr` | platforms to crawl (different regional clusters run in parallel) |
| `WINDOW_DAYS` | `14` | only matches from the last N days |
| `CRAWL_MODE` | `full` | `full` (matches + ranks), `matches-only`, `ranks-only` |
| `STRATEGY` | `stratified` | balance matches over tiers, or `snowball` from `SEED_RIOT_ID` |
| `FETCH_TIMELINES` | `true` | timelines cost one request per match (half the match throughput) |

## Commands

```bash
.venv/bin/python -m crawler crawl [--duration 12h] [--max-matches N] [--regions eun1,kr] [--mode ranks-only]
.venv/bin/python -m crawler status [--ping [PLATFORM]] [--probe PLATFORM]
.venv/bin/python -m crawler retry-parked        # retry failed (parked) items once
.venv/bin/python -m crawler ddragon [--patch 16.20]   # champion/item/rune names for a patch
.venv/bin/python -m crawler export [--formats tsv,parquet] [--tables matches,participants] [--platforms EUN1]
```

- `crawl` can be stopped any time with **Ctrl-C** (a second Ctrl-C exits immediately) and resumes where it
  stopped. Without `--duration` / `--max-matches` it runs until every tier is exhausted.
- `export` makes no API calls and regenerates `data/export/` from `data/raw/` - run it as often as you like.
  It warns when a patch has no Data Dragon files; fetch them with `ddragon --patch X.Y`.

## run.sh (Linux)

`run.sh` is a small wrapper for long, unattended crawls on Linux, e.g. on a laptop left running with the
lid closed. It:

- checks that `.venv` and `.env` exist,
- starts `python -m crawler crawl` with the arguments you pass (`./run.sh --duration 12h --mode full`),
- runs it under `systemd-inhibit`, which stops the system from suspending (lid switch, sleep, idle)
  **only while the crawler is running**. Without `systemd-inhibit` it prints a warning and runs anyway.

If the machine still suspends when the lid is closed, the desktop's own power settings take precedence:
set *When laptop lid is closed → Do nothing* (KDE: System Settings → Power Management; GNOME: Settings →
Power / `gnome-tweaks`) for the duration of the run.

Things worth checking before a long run: free disk space (raw data is roughly 70 KB per match with timeline;
the crawler pauses below `MIN_FREE_DISK_GB`), power, and a fresh API key.

## Expired or invalid key

Development keys expire after 24 hours. When Riot rejects the key, the crawler **pauses** (nothing is lost or
marked failed), logs `Crawling is PAUSED` and shows a desktop notification (`notify-send`). Put a new key into
`RIOT_API_KEY` in `.env` (locally or over SSH): the crawler re-reads the file every `KEY_POLL_SECONDS`
(30 s), or immediately when you press **Enter** in its terminal, validates the key and resumes.

Do not `export RIOT_API_KEY=...` in your shell: environment variables override `.env`, so editing the file
would then have no effect.

## Data layout

```
data/
  state.db                                   # crawl state (SQLite) - what is queued, done, parked
  raw/                                       # lossless API responses, gzip JSON Lines
    matches/platform=EUN1/part-0001.jsonl.gz
    timelines/platform=EUN1/part-0001.jsonl.gz
    ranks/platform=EUN1/part-0001.jsonl.gz
    ddragon/16.20.1/{champion,item,summoner,runesReforged}.json
  export/
    tsv/<table>/platform_id=EUN1/part-0001.tsv
    parquet/<table>/platform_id=EUN1/part-0001.parquet
    {tsv,parquet}/dim_champions/part-0001.*   # Data Dragon dimensions are not split by platform
logs/crawler.log                             # rotating, full detail
```

Tables: `matches` (1 per match), `participants` (10 per match, with rank), `teams` (2 per match),
`timeline_participant_frames` (10 per minute), `timeline_events`, `ranks` (1 per rank lookup),
`dim_champions`, `dim_items`, `dim_summoner_spells`, `dim_runes`. Column lists: `crawler/export/schemas.py`.
Join keys: `match_id`, `puuid`, `participant_id`, `champion_id`, `item0..6 → item_id`, `patch`.

Export conventions:
- `platform_id` is a partition folder (`platform_id=EUN1`). TSV files also contain it as a column; Parquet
  files do not - tools that read the table directory (Spark, pyarrow, pandas) add it back from the folder name.
- Every TSV part starts with a header line. Null is an empty field, booleans are `true`/`false`, timestamps are
  ISO-8601 UTC (`2026-10-05T12:17:53.297Z`), lists (`bans`, `assisting_participant_ids`, `tags`) are
  comma-joined, and tabs/newlines inside text are replaced by spaces.
- In Parquet, timestamps are UTC timestamp columns and lists are list columns.

## Known limitations

- **Rank is the current rank at lookup time**, not the rank during the match - the Riot API has no rank
  history. `rank_lag_hours` (lookup time − game end) tells how far apart they are; most of the lag comes from
  match age (up to `WINDOW_DAYS`), so filter on it or use a smaller window if rank accuracy matters.
- **Rank coverage**: with a dev key the platform host allows ~0.8 requests/s, about a quarter of what ranks
  for all participants would need. `full` mode looks up the newest matches first; the rest stays queued for
  `crawl --mode ranks-only`. Participants without a lookup have `rank_status = NOT_FETCHED`.
- **Every match has a tier label** anyway: `seed_tier` (tier of the player it was found through).
- **Apex tiers are small** (MASTER+ has a few thousand players per region), so they run out sooner;
  their share goes to the other tiers.
- **Duplicates**: after a crash one raw record may be written twice; the export dedupes by `match_id`
  (and ranks by `puuid` + `fetched_at`).
- `--max-matches` can overshoot by one match per region (matches already in progress finish).

## Development

```bash
.venv/bin/pytest -q      # no live API calls: fixtures + fake API + virtual clock
```
