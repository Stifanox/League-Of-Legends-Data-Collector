"""Raw records -> flat table rows (DESIGN §11). Pure functions, no I/O.

Rows are dicts keyed by column name; columns missing from a row are written as null.
Timestamps are kept as UTC epoch milliseconds (`ts` columns).
"""

from __future__ import annotations

import bisect
import json
from datetime import datetime
from typing import Any

from crawler.export.schemas import (
    DIM_CHAMPIONS,
    DIM_ITEMS,
    DIM_SUMMONER_SPELLS,
    MATCHES,
    PARTICIPANTS,
    RANKS,
    TEAMS,
    TIMELINE_EVENTS,
    TIMELINE_PARTICIPANT_FRAMES,
    Table,
)
from riot_client.ddragon import patch_of
from riot_client.routing import SOLO_QUEUE

Row = dict[str, Any]


# ----------------------------------------------------------------- generic helpers


def get_path(obj: Any, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def coerce(value: Any, type_: str) -> Any:
    """Convert a raw JSON value to the column type; anything that does not fit becomes None."""
    if value is None:
        return None
    try:
        if type_ == "str":
            return value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if type_ in ("int", "ts"):
            if isinstance(value, float) and not value.is_integer():
                return round(value)
            return int(value)
        if type_ == "float":
            return float(value)
        if type_ == "bool":
            return value if isinstance(value, bool) else bool(int(value))
        if type_ == "list_int":
            return [int(v) for v in value] if isinstance(value, list) else None
        if type_ == "list_str":
            return [str(v) for v in value] if isinstance(value, list) else None
    except (TypeError, ValueError):
        return None
    raise ValueError(f"Unknown column type {type_!r}")


def extract(table: Table, obj: dict[str, Any]) -> Row:
    """Values of every column of `table` that has a raw `source` path."""
    return {c.name: coerce(get_path(obj, c.source), c.type) for c in table.columns if c.source is not None}


def iso_to_ms(text: str | None) -> int | None:
    if not text:
        return None
    try:
        return int(datetime.fromisoformat(text).timestamp() * 1000)
    except ValueError:
        return None


def patch(game_version: str | None) -> str | None:
    """`"16.20.824.8524"` -> `"16.20"`."""
    return patch_of(game_version) if game_version else None


# ----------------------------------------------------------------- ranks


def solo_entry(entries: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    return next((e for e in entries or [] if isinstance(e, dict) and e.get("queueType") == SOLO_QUEUE), None)


def flatten_rank(record: dict[str, Any]) -> Row:
    entry = solo_entry(record.get("entries")) or {}
    row = extract(RANKS, entry)
    row.update(
        puuid=record.get("puuid"),
        platform_id=record.get("platform_id"),
        fetched_at=iso_to_ms(record.get("fetched_at")),
        rank_status=record.get("status") or ("RANKED" if entry else "UNRANKED"),
    )
    return row


class RankIndex:
    """All rank lookups per player, to pick the one closest to a match (DESIGN §11)."""

    _NONE = {
        "rank_status": "NOT_FETCHED",
        "tier": None,
        "division": None,
        "lp": None,
        "rank_wins": None,
        "rank_losses": None,
        "rank_fetched_at": None,
        "rank_lag_hours": None,
    }

    def __init__(self) -> None:
        # puuid -> sorted [(fetched_ms, status, tier, division, lp, wins, losses)]
        self._lookups: dict[str, list[tuple]] = {}

    def __len__(self) -> int:
        return sum(len(v) for v in self._lookups.values())

    def add(self, rank_row: Row) -> None:
        if not rank_row.get("puuid") or rank_row.get("fetched_at") is None:
            return
        item = (
            rank_row["fetched_at"],
            rank_row["rank_status"],
            rank_row.get("tier"),
            rank_row.get("division"),
            rank_row.get("lp"),
            rank_row.get("wins"),
            rank_row.get("losses"),
        )
        lookups = self._lookups.setdefault(rank_row["puuid"], [])
        bisect.insort(lookups, item, key=lambda x: x[0])

    def lookup(self, puuid: str | None, game_end_ms: int | None) -> Row:
        """First lookup at/after the game end, else the latest before it, else NOT_FETCHED."""
        lookups = self._lookups.get(puuid or "")
        if not lookups or game_end_ms is None:
            return dict(self._NONE)
        i = bisect.bisect_left(lookups, game_end_ms, key=lambda x: x[0])
        fetched, status, tier, division, lp, wins, losses = lookups[i] if i < len(lookups) else lookups[-1]
        return {
            "rank_status": status,
            "tier": tier,
            "division": division,
            "lp": lp,
            "rank_wins": wins,
            "rank_losses": losses,
            "rank_fetched_at": fetched,
            "rank_lag_hours": round((fetched - game_end_ms) / 3_600_000, 3),
        }


# ----------------------------------------------------------------- matches


def game_end_ms(info: dict[str, Any]) -> int | None:
    end = info.get("gameEndTimestamp")
    if isinstance(end, (int, float)):
        return int(end)
    start, duration = info.get("gameStartTimestamp"), info.get("gameDuration")
    if isinstance(start, (int, float)) and isinstance(duration, (int, float)):
        return int(start + duration * 1000)
    return None


def flatten_match(record: dict[str, Any], ranks: RankIndex | None = None) -> dict[str, list[Row]]:
    """One raw match record -> rows for `matches` (1), `participants` (10) and `teams` (2)."""
    data = record.get("data") or {}
    info = data.get("info") or {}
    participants = [p for p in info.get("participants") or [] if isinstance(p, dict)]
    teams = [t for t in info.get("teams") or [] if isinstance(t, dict)]
    common = {
        "match_id": record.get("match_id") or get_path(data, ("metadata", "matchId")),
        "platform_id": record.get("platform_id"),
        "patch": patch(info.get("gameVersion")),
    }
    end_ms = game_end_ms(info)

    match_row = extract(MATCHES, info)
    match_row.update(
        common,
        data_version=coerce(get_path(data, ("metadata", "dataVersion")), "str"),
        game_creation=coerce(info.get("gameCreation"), "ts"),
        game_start=coerce(info.get("gameStartTimestamp"), "ts"),
        game_end=end_ms,
        winning_team=next((coerce(t.get("teamId"), "int") for t in teams if t.get("win") is True), None),
        surrender=any(p.get("gameEndedInSurrender") is True for p in participants),
        early_surrender=any(p.get("gameEndedInEarlySurrender") is True for p in participants),
        seed_tier=record.get("seed_tier"),
        seed_puuid=record.get("seed_puuid"),
        strategy=record.get("strategy"),
        fetched_at=iso_to_ms(record.get("fetched_at")),
    )

    participant_rows = []
    for participant in participants:
        row = extract(PARTICIPANTS, participant)
        row.update(common)
        row.update(_perks(participant.get("perks")))
        rank = ranks.lookup(row.get("puuid"), end_ms) if ranks is not None else dict(RankIndex._NONE)
        row.update(rank)
        participant_rows.append(row)

    team_rows = []
    for team in teams:
        row = extract(TEAMS, team)
        row.update(common)
        bans = sorted((b for b in team.get("bans") or [] if isinstance(b, dict)), key=lambda b: b.get("pickTurn", 0))
        row["bans"] = coerce([b.get("championId") for b in bans], "list_int")
        team_rows.append(row)

    return {"matches": [match_row], "participants": participant_rows, "teams": team_rows}


def _perks(perks: Any) -> Row:
    """primary/sub style and the 6 chosen runes (4 primary + 2 secondary)."""
    row: Row = {"primary_style": None, "sub_style": None, **{f"perk{i}": None for i in range(6)}}
    if not isinstance(perks, dict):
        return row
    styles = {s.get("description"): s for s in perks.get("styles") or [] if isinstance(s, dict)}
    primary, sub = styles.get("primaryStyle") or {}, styles.get("subStyle") or {}
    row["primary_style"] = coerce(primary.get("style"), "int")
    row["sub_style"] = coerce(sub.get("style"), "int")
    selections = (primary.get("selections") or [])[:4] + (sub.get("selections") or [])[:2]
    for i, selection in enumerate(selections):
        if isinstance(selection, dict):
            row[f"perk{i}"] = coerce(selection.get("perk"), "int")
    return row


# ----------------------------------------------------------------- timelines


def flatten_timeline(record: dict[str, Any], patch_value: str | None = None) -> dict[str, list[Row]]:
    """One raw timeline -> `timeline_participant_frames` (minutes x 10) and `timeline_events` rows."""
    data = record.get("data") or {}
    info = data.get("info") or {}
    common = {
        "match_id": record.get("match_id") or get_path(data, ("metadata", "matchId")),
        "platform_id": record.get("platform_id"),
        "patch": patch_value,
    }
    puuids = _timeline_puuids(data)

    frame_rows, event_rows = [], []
    event_index = 0
    for minute, frame in enumerate(info.get("frames") or []):
        if not isinstance(frame, dict):
            continue
        timestamp = coerce(frame.get("timestamp"), "int")
        frames = frame.get("participantFrames") or {}
        for key in sorted(frames, key=lambda k: int(k) if str(k).isdigit() else 0):
            participant_frame = frames[key]
            if not isinstance(participant_frame, dict):
                continue
            row = extract(TIMELINE_PARTICIPANT_FRAMES, participant_frame)
            participant_id = row.get("participant_id") or coerce(key, "int")
            row.update(
                common,
                minute=minute,
                timestamp_ms=timestamp,
                participant_id=participant_id,
                puuid=puuids.get(participant_id),
                team_id=_team_of(participant_id),
            )
            frame_rows.append(row)
        for event in frame.get("events") or []:
            if not isinstance(event, dict):
                continue
            row = extract(TIMELINE_EVENTS, event)
            row.update(
                common,
                event_index=event_index,
                minute=minute,
                assisting_participant_ids=coerce(event.get("assistingParticipantIds"), "list_int"),
            )
            event_rows.append(row)
            event_index += 1
    return {"timeline_participant_frames": frame_rows, "timeline_events": event_rows}


def _timeline_puuids(data: dict[str, Any]) -> dict[int, str]:
    listed = get_path(data, ("info", "participants"))
    if isinstance(listed, list) and listed:
        return {p["participantId"]: p.get("puuid") for p in listed if isinstance(p, dict) and "participantId" in p}
    ordered = get_path(data, ("metadata", "participants")) or []
    return {i + 1: puuid for i, puuid in enumerate(ordered)}


def _team_of(participant_id: int | None) -> int | None:
    if participant_id is None or not 1 <= participant_id <= 10:
        return None
    return 100 if participant_id <= 5 else 200


# ----------------------------------------------------------------- Data Dragon


def flatten_ddragon(version: str, files: dict[str, Any]) -> dict[str, list[Row]]:
    """Data Dragon JSON (`champion.json`, `item.json`, `summoner.json`, `runesReforged.json`) -> dim rows.

    `files` maps file name to parsed JSON; missing files produce no rows.
    """
    common = {"ddragon_version": version, "patch": patch_of(version)}
    out: dict[str, list[Row]] = {"dim_champions": [], "dim_items": [], "dim_summoner_spells": [], "dim_runes": []}

    for champion in (get_path(files.get("champion.json"), ("data",)) or {}).values():
        row = extract(DIM_CHAMPIONS, champion)
        row.update(common, champion_id=coerce(champion.get("key"), "int"))
        out["dim_champions"].append(row)

    for item_id, item in (get_path(files.get("item.json"), ("data",)) or {}).items():
        row = extract(DIM_ITEMS, item)
        row.update(
            common,
            item_id=coerce(item_id, "int"),
            from_items=coerce(item.get("from"), "list_int"),
            into_items=coerce(item.get("into"), "list_int"),
            stats_json=json.dumps(item.get("stats") or {}, sort_keys=True),
        )
        out["dim_items"].append(row)

    for spell in (get_path(files.get("summoner.json"), ("data",)) or {}).values():
        row = extract(DIM_SUMMONER_SPELLS, spell)
        cooldown = spell.get("cooldown")
        row.update(
            common,
            spell_id=coerce(spell.get("key"), "int"),
            cooldown=coerce(cooldown[0] if isinstance(cooldown, list) and cooldown else None, "float"),
        )
        out["dim_summoner_spells"].append(row)

    for style in files.get("runesReforged.json") or []:
        if not isinstance(style, dict):
            continue
        for slot, group in enumerate(style.get("slots") or []):
            for rune in (group or {}).get("runes") or []:
                out["dim_runes"].append(
                    {
                        **common,
                        "rune_id": coerce(rune.get("id"), "int"),
                        "rune_key": rune.get("key"),
                        "name": rune.get("name"),
                        "style_id": coerce(style.get("id"), "int"),
                        "style_name": style.get("name"),
                        "slot": slot,
                    }
                )
    return out
