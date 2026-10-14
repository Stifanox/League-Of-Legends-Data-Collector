"""Schema of `state.db` and its migrations (version kept in `PRAGMA user_version`)."""

from __future__ import annotations

from crawler.state.db import Database

# Each entry upgrades the schema by one version; never edit an entry once released.
MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE meta (
        key   TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE matches (
        match_id      TEXT PRIMARY KEY,
        platform_id   TEXT NOT NULL,
        status        TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'done', 'parked')),
        match_done    INTEGER NOT NULL DEFAULT 0,
        timeline_done INTEGER NOT NULL DEFAULT 0,
        seed_tier     TEXT,
        seed_puuid    TEXT,
        strategy      TEXT,
        queued_at     REAL NOT NULL,
        updated_at    REAL NOT NULL
    );
    CREATE INDEX matches_by_status ON matches (platform_id, status, queued_at);

    CREATE TABLE players (
        platform_id TEXT NOT NULL,
        puuid       TEXT NOT NULL,
        tier        TEXT,
        source      TEXT NOT NULL,
        added_at    REAL NOT NULL,
        visited_at  REAL,
        visits      INTEGER NOT NULL DEFAULT 0,
        next_start  INTEGER NOT NULL DEFAULT 0,
        exhausted   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (platform_id, puuid)
    );
    CREATE INDEX players_by_tier ON players (platform_id, tier, exhausted, visited_at);

    CREATE TABLE tier_progress (
        platform_id TEXT NOT NULL,
        tier        TEXT NOT NULL,
        collected   INTEGER NOT NULL DEFAULT 0,
        exhausted   INTEGER NOT NULL DEFAULT 0,
        apex_round  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (platform_id, tier)
    );

    CREATE TABLE league_pages (
        platform_id TEXT NOT NULL,
        tier        TEXT NOT NULL,
        division    TEXT NOT NULL,
        page        INTEGER NOT NULL,
        players     INTEGER NOT NULL,
        fetched_at  REAL NOT NULL,
        PRIMARY KEY (platform_id, tier, division, page)
    );

    CREATE TABLE rank_lookups (
        id          INTEGER PRIMARY KEY,
        platform_id TEXT NOT NULL,
        puuid       TEXT NOT NULL,
        fetched_at  REAL NOT NULL,
        status      TEXT NOT NULL
    );
    CREATE INDEX rank_lookups_by_player ON rank_lookups (platform_id, puuid, fetched_at);

    CREATE TABLE rank_queue (
        platform_id TEXT NOT NULL,
        puuid       TEXT NOT NULL,
        enqueued_at REAL NOT NULL,
        PRIMARY KEY (platform_id, puuid)
    );

    CREATE TABLE parked (
        kind         TEXT NOT NULL CHECK (kind IN ('match', 'timeline', 'rank', 'ids', 'league')),
        platform_id  TEXT NOT NULL,
        key          TEXT NOT NULL,
        error        TEXT,
        status_code  INTEGER,
        attempts     INTEGER NOT NULL DEFAULT 1,
        first_parked REAL NOT NULL,
        last_attempt REAL NOT NULL,
        PRIMARY KEY (kind, platform_id, key)
    );
    """,
]


def schema_version(db: Database) -> int:
    return db.scalar("PRAGMA user_version")


def migrate(db: Database) -> None:
    """Apply missing migrations; refuse a database created by newer code."""
    version = schema_version(db)
    if version > len(MIGRATIONS):
        raise RuntimeError(f"{db.path} has schema v{version}, newer than this code (v{len(MIGRATIONS)})")
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        # executescript commits on its own, so the version bump is part of the same script.
        db.conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")
