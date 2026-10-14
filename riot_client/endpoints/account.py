"""account-v1 (regional hosts). Used only to seed the snowball strategy."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from riot_client.endpoints import JsonGetter, expect

BY_RIOT_ID = "account-v1.by-riot-id"


def parse_riot_id(riot_id: str) -> tuple[str, str]:
    """Split `"Game Name#TAG"` into `("Game Name", "TAG")`."""
    game_name, sep, tag_line = riot_id.strip().rpartition("#")
    if not sep or not game_name.strip() or not tag_line.strip():
        raise ValueError(f"Riot ID must look like GameName#TAG, got {riot_id!r}")
    return game_name.strip(), tag_line.strip()


async def account_by_riot_id(client: JsonGetter, region: str, game_name: str, tag_line: str) -> dict[str, Any]:
    """`{"puuid", "gameName", "tagLine"}` of a Riot account."""
    data = await client.get_json(
        region,
        f"/riot/account/v1/accounts/by-riot-id/{quote(game_name, safe='')}/{quote(tag_line, safe='')}",
        method_key=BY_RIOT_ID,
    )
    return expect(data, lambda v: isinstance(v, dict) and isinstance(v.get("puuid"), str), "account", BY_RIOT_ID)
