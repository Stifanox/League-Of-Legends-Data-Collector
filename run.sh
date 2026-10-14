#!/usr/bin/env bash
# Runs the crawler and keeps the laptop awake (also with the lid closed) while it runs.
# Arguments are passed to `python -m crawler crawl`, e.g. ./run.sh --duration 48h
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if [[ ! -x .venv/bin/python ]]; then
    echo "Missing .venv - create it first:" >&2
    echo "  python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi
if [[ ! -f .env ]]; then
    echo "Missing .env - copy .env.example to .env and set RIOT_API_KEY" >&2
    exit 1
fi

cmd=(.venv/bin/python -m crawler crawl "$@")

if command -v systemd-inhibit >/dev/null 2>&1; then
    exec systemd-inhibit \
        --what=handle-lid-switch:sleep:idle \
        --who="lol-crawler" \
        --why="Collecting Riot API data" \
        "${cmd[@]}"
fi

echo "WARNING: systemd-inhibit not found - the system may suspend (e.g. on lid close) while crawling" >&2
exec "${cmd[@]}"
