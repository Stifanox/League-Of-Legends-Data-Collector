"""`status --probe`: one pass through every endpoint the crawler uses (manual smoke check).

With `--save-fixtures DIR` it also stores the responses used as test fixtures.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from crawler.config import Settings
from crawler.riot import build_client
from riot_client.endpoints import account, league, match
from riot_client.routing import SOLO_QUEUE_ID, account_region_of, region_of

FIXTURE_LEAGUE_ENTRIES = 5


async def probe(settings: Settings, env_file: Path, platform: str, save_dir: Path | None = None) -> None:
    region = region_of(platform)
    async with build_client(settings, env_file, probe_host=platform) as client:
        challengers = await league.apex_league(client, platform, "CHALLENGER")
        puuids = league.league_puuids(challengers)
        _show("CHALLENGER league", challengers, f"{len(puuids)} players")

        page = await league.entries_page(client, platform, "GOLD", "II", page=1)
        _show("GOLD II page 1", page, f"{len(page)} entries")

        end = int(time.time())
        start = end - settings.window_days * 86400
        match_id = None
        for puuid in puuids:
            ids = await match.match_ids_by_puuid(
                client, region, puuid, queue=SOLO_QUEUE_ID, start_time=start, end_time=end, count=1
            )
            if ids:
                match_id = ids[0]
                break
        if match_id is None:
            raise SystemExit("No CHALLENGER player has a Solo/Duo match in the window")
        print(f"  match id: {match_id}")

        match_data = await match.match(client, region, match_id)
        _show("match", match_data, f"{len(match_data['info'].get('participants', []))} participants")
        timeline_data = await match.timeline(client, region, match_id)
        _show("timeline", timeline_data, f"{len(timeline_data['info'].get('frames', []))} frames")

        player = match_data["metadata"]["participants"][0]
        entries = await league.entries_by_puuid(client, platform, player)
        _show("rank of participant 0", entries, str(league.solo_queue_rank(entries)))

        participant = match_data["info"]["participants"][0]
        riot_id = f"{participant.get('riotIdGameName')}#{participant.get('riotIdTagline')}"
        acc = await account.account_by_riot_id(client, account_region_of(platform), *account.parse_riot_id(riot_id))
        _show("account by Riot ID", acc, "puuid matches" if acc["puuid"] == player else "puuid DIFFERS")

        ranked, unranked = None, None
        if save_dir is not None:
            # Find one ranked and one unranked response among the participants.
            for puuid in match_data["metadata"]["participants"]:
                found = entries if puuid == player else await league.entries_by_puuid(client, platform, puuid)
                if league.solo_queue_rank(found)["status"] == "RANKED":
                    ranked = ranked or found
                else:
                    unranked = unranked if unranked is not None else found
                if ranked is not None and unranked is not None:
                    break

    if save_dir is not None:
        challengers_trimmed = {**challengers, "entries": challengers["entries"][:FIXTURE_LEAGUE_ENTRIES]}
        fixtures = {
            "match.json": match_data,
            "timeline.json": timeline_data,
            "league_entries_page.json": page[:FIXTURE_LEAGUE_ENTRIES],
            "apex_league.json": challengers_trimmed,
            "entries_by_puuid_ranked.json": ranked if ranked is not None else entries,
            "entries_by_puuid_empty.json": unranked if unranked is not None else [],
            "account.json": acc,
        }
        save_dir.mkdir(parents=True, exist_ok=True)
        for name, data in fixtures.items():
            (save_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"\nSaved {len(fixtures)} fixtures to {save_dir}"
              + ("" if unranked is not None else " (no unranked participant found; empty response written as [])"))

    print("\nRate limits learned (used/limit per window):")
    for host, limiter in client.limiters.items():
        state = limiter.describe()
        print(f"  {host} app: {format_windows(state['app'])}")
        for method, windows in state["methods"].items():
            print(f"  {host} {method}: {format_windows(windows)}")


def _show(label: str, data: Any, detail: str) -> None:
    size = len(json.dumps(data, ensure_ascii=False).encode())
    print(f"  {label}: {size / 1024:.1f} KiB, {detail}")


def format_windows(windows: list[tuple[int, int, int]]) -> str:
    return ", ".join(f"{count}/{limit} per {seconds}s" for count, limit, seconds in windows) or "-"
