"""Command line entry point: `python -m crawler <command>`."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from crawler.config import ConfigError, Settings, load_settings
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
    sub.add_parser("status", help="show settings and crawl progress")

    ddragon = sub.add_parser("ddragon", help="download Data Dragon static data")
    ddragon.add_argument("--patch", metavar="X.Y", help="patch to download (default: latest)")
    return parser


def cmd_status(settings: Settings) -> int:
    print("Settings:")
    width = max(len(name) for name, _ in settings.describe())
    for name, value in settings.describe():
        print(f"  {name:<{width}}  {value}")
    log.debug("status: settings loaded, key %s", settings.masked_key)
    return 0


def cmd_not_implemented(command: str) -> int:
    print(f"{command}: not implemented yet", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # Only commands that call the API need the key.
    require_key = args.command in {"crawl", "retry-parked"}
    try:
        settings = load_settings(args.env_file, require_key=require_key)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    setup_logging(settings.log_dir)

    if args.command == "status":
        return cmd_status(settings)
    return cmd_not_implemented(args.command)


if __name__ == "__main__":
    sys.exit(main())
