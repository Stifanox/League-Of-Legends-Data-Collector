"""league-v4 (platform hosts)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from riot_client.endpoints import JsonGetter, expect
from riot_client.routing import APEX_TIERS, DIVISION_TIERS, DIVISIONS, SOLO_QUEUE

BY_PUUID = "league-v4.by-puuid"
ENTRIES = "league-v4.entries"
APEX = "league-v4.apex"

_APEX_PATHS = {
    "CHALLENGER": "challengerleagues",
    "GRANDMASTER": "grandmasterleagues",
    "MASTER": "masterleagues",
}


async def entries_by_puuid(client: JsonGetter, platform: str, puuid: str) -> list[dict[str, Any]]:
    """All ranked entries of a player (one per queue); `[]` when unranked everywhere."""
    data = await client.get_json(
        platform, f"/lol/league/v4/entries/by-puuid/{quote(puuid, safe='')}", method_key=BY_PUUID
    )
    return expect(data, _is_entry_list, "list of league entries", BY_PUUID)


def solo_queue_rank(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Solo/Duo rank from `entries_by_puuid`: status `RANKED` or `UNRANKED` plus tier fields."""
    for entry in entries:
        if entry.get("queueType") == SOLO_QUEUE:
            return {
                "status": "RANKED",
                "tier": entry.get("tier"),
                "division": entry.get("rank"),
                "lp": entry.get("leaguePoints"),
                "wins": entry.get("wins"),
                "losses": entry.get("losses"),
            }
    return {"status": "UNRANKED", "tier": None, "division": None, "lp": None, "wins": None, "losses": None}


async def entries_page(
    client: JsonGetter, platform: str, tier: str, division: str, page: int = 1
) -> list[dict[str, Any]]:
    """One page of Solo/Duo entries for IRON..DIAMOND; `[]` past the last page."""
    tier, division = tier.upper(), division.upper()
    if tier not in DIVISION_TIERS:
        raise ValueError(f"entries_page supports {', '.join(DIVISION_TIERS)}, got {tier!r}")
    if division not in DIVISIONS:
        raise ValueError(f"division must be one of {', '.join(DIVISIONS)}, got {division!r}")
    if page < 1:
        raise ValueError(f"page must be >= 1, got {page}")
    data = await client.get_json(
        platform, f"/lol/league/v4/entries/{SOLO_QUEUE}/{tier}/{division}", {"page": page}, method_key=ENTRIES
    )
    return expect(data, _is_entry_list, "list of league entries", ENTRIES)


async def apex_league(client: JsonGetter, platform: str, tier: str) -> dict[str, Any]:
    """The whole Solo/Duo MASTER, GRANDMASTER or CHALLENGER league of a platform."""
    tier = tier.upper()
    if tier not in APEX_TIERS:
        raise ValueError(f"apex_league supports {', '.join(APEX_TIERS)}, got {tier!r}")
    data = await client.get_json(
        platform, f"/lol/league/v4/{_APEX_PATHS[tier]}/by-queue/{SOLO_QUEUE}", method_key=APEX
    )
    return expect(
        data, lambda v: isinstance(v, dict) and _is_entry_list(v.get("entries")), "league with entries", APEX
    )


def league_puuids(response: list[dict[str, Any]] | dict[str, Any]) -> list[str]:
    """PUUIDs from an `entries_page` list or an `apex_league` object, in response order."""
    entries = response.get("entries", []) if isinstance(response, dict) else response
    return [e["puuid"] for e in entries if isinstance(e.get("puuid"), str) and e["puuid"]]


def _is_entry_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(e, dict) for e in value)
