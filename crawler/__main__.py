"""Command line entry point: `python -m crawler <command>`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from crawler.config import CRAWL_MODES, PLATFORM_TO_REGION, ConfigError, Settings, load_settings
from crawler.logging_setup import setup_logging

log = logging.getLogger("crawler")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m crawler",
        description="Riot API crawler for League of Legends Ranked Solo/Duo matches.",
    )
    parser.add_argument("--env-file", type=Path, default=Path(".env"), help="settings file (default: .env)")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    crawl = sub.add_parser("crawl", help="collect matches, timelines and ranks")
    crawl.add_argument("--max-matches", type=int, metavar="N", help="stop after N new matches")
    crawl.add_argument("--duration", metavar="48h", help="stop after this long (e.g. 90m, 48h)")
    crawl.add_argument("--regions", metavar="eun1,...", help="override REGIONS for this run")
    crawl.add_argument(
        "--mode",
        choices=CRAWL_MODES,
        help="override CRAWL_MODE: full (matches + ranks), matches-only, ranks-only",
    )

    export = sub.add_parser("export", help="write TSV/Parquet tables from raw data (no API calls)")
    export.add_argument("--formats", default="tsv,parquet", metavar="tsv,parquet", help="output formats")
    export.add_argument("--tables", metavar="a,b", help="only these tables")
    export.add_argument("--platforms", metavar="EUN1,...", help="only these platforms")

    sub.add_parser("retry-parked", help="retry parked items once")
    status = sub.add_parser("status", help="show settings and crawl progress")
    status.add_argument(
        "--ping",
        nargs="?",
        const="",
        metavar="PLATFORM",
        help="call lol-status on PLATFORM (default: first of REGIONS) and show rate limits",
    )
    status.add_argument(
        "--probe",
        metavar="PLATFORM",
        help="smoke check: league, match ids, match, timeline, rank and account calls on PLATFORM",
    )
    status.add_argument(
        "--save-fixtures",
        type=Path,
        metavar="DIR",
        help="with --probe: save the responses as test fixtures into DIR",
    )
    status.add_argument(
        "--persist",
        type=int,
        metavar="N",
        help="with --probe: also store N matches (+ timelines, + one rank) in DATA_DIR like a crawl",
    )

    ddragon = sub.add_parser("ddragon", help="download Data Dragon static data")
    ddragon.add_argument("--patch", metavar="X.Y", help="patch to download (default: latest)")
    return parser


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    print("Settings:")
    width = max(len(name) for name, _ in settings.describe())
    for name, value in settings.describe():
        print(f"  {name:<{width}}  {value}")
    log.debug("status: settings loaded, key %s", settings.masked_key)
    _print_state(settings)
    if args.ping is not None:
        return asyncio.run(_ping(settings, args.env_file, args.ping or settings.regions[0]))
    if args.probe is not None:
        from crawler.probe import probe

        platform = args.probe.lower()
        if not _known_platform(platform):
            return 2
        from riot_client.errors import RiotApiError

        print(f"\nProbing {platform}:")
        try:
            asyncio.run(probe(settings, args.env_file, platform, args.save_fixtures))
            if args.persist:
                from crawler.probe import persist_sample

                print(f"\nStoring {args.persist} sample match(es):")
                asyncio.run(persist_sample(settings, args.env_file, platform, args.persist))
                _print_state(settings)
        except RiotApiError as exc:
            print(f"\nProbe failed: {exc}", file=sys.stderr)
            return 1
    return 0


def _print_state(settings: Settings) -> None:
    from crawler.shutdown import free_gb
    from crawler.state import StateStore

    print(f"\nDisk: {free_gb(settings.data_dir):.1f} GiB free (minimum {settings.min_free_disk_gb:g})")
    db_path = settings.data_dir / "state.db"
    if not db_path.exists():
        print(f"State: no {db_path} yet")
        return
    with StateStore(db_path) as state:
        summary = state.summary()
        last_key_error = state.meta.get("last_key_error")
    print(f"State ({db_path}):")
    for platform_id, s in summary["platforms"].items():
        print(
            f"  {platform_id}: matches {s['matches_done']} done / {s['matches_queued']} queued / "
            f"{s['matches_parked']} parked, timelines {s['timelines_done']}, "
            f"ranks {s['rank_lookups']} (+{s['rank_queue']} queued), "
            f"players {s['players_visited']}/{s['players']} visited"
        )
        tiers = [f"{t.tier} {t.collected}{'(x)' if t.exhausted else ''}" for t in s["tiers"] if
                 t.collected or t.exhausted]
        if tiers:
            print("    tiers: " + ", ".join(tiers))
    parked = summary["parked"]
    print("  parked: " + (", ".join(f"{kind} {n}" for kind, n in sorted(parked.items())) if parked else "none"))
    if last_key_error:
        print(f"  last key error: {last_key_error}")


def _known_platform(platform: str) -> bool:
    if platform in PLATFORM_TO_REGION:
        return True
    print(f"Unknown platform {platform!r}; known: {', '.join(PLATFORM_TO_REGION)}", file=sys.stderr)
    return False


def cmd_ddragon(settings: Settings, patch: str | None) -> int:
    from riot_client.ddragon import DataDragon

    async def run() -> None:
        async with DataDragon() as ddragon:
            version = await ddragon.resolve_version(patch)
            dest = settings.data_dir / "raw" / "ddragon" / version
            written = await ddragon.download(version, dest)
            print(f"Data Dragon {version}: {len(written)} file(s) downloaded into {dest}"
                  + ("" if written else " (already present)"))

    try:
        asyncio.run(run())
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


async def _ping(settings: Settings, env_file: Path, platform: str) -> int:
    """Smoke check: one cheap API call through the full client, then the learned rate limits."""
    from crawler.probe import format_windows
    from crawler.riot import build_client
    from riot_client.errors import RiotApiError
    from riot_client.http import STATUS_METHOD_KEY, STATUS_PATH

    platform = platform.lower()
    if not _known_platform(platform):
        return 2
    async with build_client(settings, env_file, probe_host=platform) as client:
        try:
            data = await client.get_json(platform, STATUS_PATH, method_key=STATUS_METHOD_KEY)
        except RiotApiError as exc:
            print(f"\nPing failed: {exc}", file=sys.stderr)
            return 1
        print(f"\nPing {platform}: OK - {data.get('name', '?')}, "
              f"{len(data.get('maintenances', []))} maintenance(s), {len(data.get('incidents', []))} incident(s)")
        for host, limiter in client.limiters.items():
            state = limiter.describe()
            print(f"Rate limits on {host} (used/limit per window):")
            print("  app:    " + format_windows(state["app"]))
            for method, windows in state["methods"].items():
                print(f"  {method}: " + format_windows(windows))
    return 0


def cmd_crawl(settings: Settings, args: argparse.Namespace) -> int:
    from crawler.run import crawl, parse_duration

    try:
        duration = parse_duration(args.duration) if args.duration else None
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    platforms = None
    if args.regions:
        platforms = [p.strip().lower() for p in args.regions.split(",") if p.strip()]
        if not platforms or not all(_known_platform(p) for p in platforms):
            return 2
    if args.max_matches is not None and args.max_matches < 1:
        print("--max-matches must be >= 1", file=sys.stderr)
        return 2
    ctx = asyncio.run(
        crawl(
            settings,
            args.env_file,
            max_matches=args.max_matches,
            duration=duration,
            platforms=platforms,
            mode=args.mode,
        )
    )
    _print_run(ctx)
    _print_state(settings)
    return 0


def cmd_retry_parked(settings: Settings, args: argparse.Namespace) -> int:
    from crawler.run import retry_parked

    if not (settings.data_dir / "state.db").exists():
        print("Nothing to retry: no state.db yet")
        return 0
    ctx = asyncio.run(retry_parked(settings, args.env_file))
    _print_run(ctx)
    _print_state(settings)
    return 0


def _print_run(ctx) -> None:
    print("\nThis run:")
    for platform_id, s in sorted(ctx.stats.items()):
        ranks = ", ".join(f"{k.lower()} {v}" for k, v in sorted(s.rank_status.items())) or "-"
        print(f"  {platform_id}: {s.matches} matches, {s.timelines} timelines, {s.ranks} ranks ({ranks}), {s.parked} parked")


def cmd_export(settings: Settings, args: argparse.Namespace) -> int:
    from crawler.export.run import export
    from crawler.export.schemas import TABLES
    from crawler.export.writers import FORMATS

    formats = tuple(_split(args.formats))
    tables = tuple(_split(args.tables)) if args.tables else None
    platforms = tuple(p.upper() for p in _split(args.platforms)) if args.platforms else None
    bad = [f for f in formats if f not in FORMATS] + [t for t in tables or () if t not in TABLES]
    if bad or not formats:
        print(f"Unknown format/table {bad}; formats: {', '.join(FORMATS)}; tables: {', '.join(TABLES)}", file=sys.stderr)
        return 2
    if not (settings.data_dir / "raw").is_dir():
        print(f"Nothing to export: no {settings.data_dir / 'raw'}", file=sys.stderr)
        return 1
    summary = export(settings.data_dir, formats=formats, tables=tables, platforms=platforms)
    _print_export(settings, summary, formats)
    return 0


def _split(text: str | None) -> list[str]:
    return [part.strip() for part in (text or "").split(",") if part.strip()]


def _print_export(settings: Settings, summary, formats: tuple[str, ...]) -> None:
    print(f"\nExported to {settings.data_dir / 'export'} ({', '.join(formats)}):")
    by_table: dict[str, list[str]] = {}
    for (table, platform_id), rows in sorted(summary.rows.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        by_table.setdefault(table, []).append(f"{platform_id or 'all'} {rows}")
    for table, counts in by_table.items():
        print(f"  {table}: {', '.join(counts)}")
    print(f"Matches: {summary.matches} (duplicates skipped: {summary.duplicate_matches})")
    if summary.matches_without_timeline is not None:
        print(f"Timelines: {summary.timelines} (matches without timeline: {summary.matches_without_timeline})")
    if summary.rank_status:
        print("Participant rank_status: " + ", ".join(f"{k} {v}" for k, v in sorted(summary.rank_status.items())))
    lags = summary.lag_percentiles()
    if lags:
        print("rank_lag_hours: " + ", ".join(f"{k} {v:.2f}" for k, v in lags.items()))
    print("Patches: " + (", ".join(f"{k} {v}" for k, v in sorted(summary.patches.items(), key=lambda kv: kv[0] or "")) or "-"))
    if summary.patches_without_ddragon:
        missing = ", ".join(summary.patches_without_ddragon)
        print(f"Missing Data Dragon for patch(es) {missing}: python -m crawler ddragon --patch <X.Y>")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Only commands that call the API need the key.
    require_key = args.command in {"crawl", "retry-parked"} or (
        args.command == "status" and (args.ping is not None or args.probe is not None)
    )
    try:
        settings = load_settings(args.env_file, require_key=require_key)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    setup_logging(settings.log_dir)

    if args.command == "status":
        return cmd_status(settings, args)
    if args.command == "ddragon":
        return cmd_ddragon(settings, args.patch)
    if args.command == "crawl":
        return cmd_crawl(settings, args)
    if args.command == "retry-parked":
        return cmd_retry_parked(settings, args)
    if args.command == "export":
        return cmd_export(settings, args)
    raise AssertionError(f"unhandled command {args.command}")  # argparse only allows the commands above


if __name__ == "__main__":
    sys.exit(main())
