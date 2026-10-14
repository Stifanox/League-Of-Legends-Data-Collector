"""Command line entry point: `python -m crawler <command>`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from crawler.config import PLATFORM_TO_REGION, ConfigError, Settings, load_settings
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

    ddragon = sub.add_parser("ddragon", help="download Data Dragon static data")
    ddragon.add_argument("--patch", metavar="X.Y", help="patch to download (default: latest)")
    return parser


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    print("Settings:")
    width = max(len(name) for name, _ in settings.describe())
    for name, value in settings.describe():
        print(f"  {name:<{width}}  {value}")
    log.debug("status: settings loaded, key %s", settings.masked_key)
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
        except RiotApiError as exc:
            print(f"\nProbe failed: {exc}", file=sys.stderr)
            return 1
    return 0


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


def cmd_not_implemented(command: str) -> int:
    print(f"{command}: not implemented yet", file=sys.stderr)
    return 1


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
    return cmd_not_implemented(args.command)


if __name__ == "__main__":
    sys.exit(main())
