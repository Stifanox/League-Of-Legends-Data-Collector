"""match-v5 (regional hosts)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from riot_client.endpoints import JsonGetter, expect

IDS = "match-v5.ids"
MATCH = "match-v5.match"
TIMELINE = "match-v5.timeline"

MAX_COUNT = 100


async def match_ids_by_puuid(
    client: JsonGetter,
    region: str,
    puuid: str,
    *,
    queue: int | None = None,
    start_time: int | None = None,
    end_time: int | None = None,
    start: int = 0,
    count: int = 20,
) -> list[str]:
    """Match ids of a player, newest first. Times are epoch seconds."""
    if not 1 <= count <= MAX_COUNT:
        raise ValueError(f"count must be 1..{MAX_COUNT}, got {count}")
    if start < 0:
        raise ValueError(f"start must be >= 0, got {start}")
    params: dict[str, int] = {"start": start, "count": count}
    if queue is not None:
        params["queue"] = queue
    if start_time is not None:
        params["startTime"] = start_time
    if end_time is not None:
        params["endTime"] = end_time
    ids = await client.get_json(
        region, f"/lol/match/v5/matches/by-puuid/{quote(puuid, safe='')}/ids", params, method_key=IDS
    )
    return expect(ids, lambda v: isinstance(v, list) and all(isinstance(i, str) for i in v), "list of ids", IDS)


async def match(client: JsonGetter, region: str, match_id: str) -> dict[str, Any]:
    """Full match-v5 match JSON."""
    data = await client.get_json(region, f"/lol/match/v5/matches/{quote(match_id, safe='')}", method_key=MATCH)
    return expect(data, _has_metadata_and_info, "match with metadata and info", MATCH)


async def timeline(client: JsonGetter, region: str, match_id: str) -> dict[str, Any]:
    """match-v5 timeline JSON (per-minute frames and events)."""
    data = await client.get_json(
        region, f"/lol/match/v5/matches/{quote(match_id, safe='')}/timeline", method_key=TIMELINE
    )
    return expect(data, _has_metadata_and_info, "timeline with metadata and info", TIMELINE)


def _has_metadata_and_info(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("metadata"), dict) and isinstance(value.get("info"), dict)
