"""Export tables (DESIGN §11): one schema per table drives both the TSV and the Parquet writer.

A column has a name, a type, and optionally a `source` path into the raw JSON object the
table is flattened from (e.g. `("challenges", "kda")`). Columns without a source are
computed in `flatten.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pyarrow as pa

# Column types: name -> pyarrow type. Timestamps are UTC milliseconds.
TYPES: dict[str, pa.DataType] = {
    "str": pa.string(),
    "int": pa.int64(),
    "float": pa.float64(),
    "bool": pa.bool_(),
    "ts": pa.timestamp("ms", tz="UTC"),
    "list_int": pa.list_(pa.int64()),
    "list_str": pa.list_(pa.string()),
}


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    source: tuple[str, ...] | None = None

    @property
    def arrow_type(self) -> pa.DataType:
        return TYPES[self.type]


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    # Partitioned tables go to `<table>/platform_id=XXX/`; dimension tables are not partitioned.
    partitioned: bool = True

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def arrow_schema(self, *, without: tuple[str, ...] = ()) -> pa.Schema:
        return pa.schema([pa.field(c.name, c.arrow_type) for c in self.columns if c.name not in without])


def snake(name: str) -> str:
    """`totalDamageDealtToChampions` -> `total_damage_dealt_to_champions`."""
    name = name.replace("CCing", "Ccing")  # timeCCingOthers -> time_ccing_others
    name = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    return re.sub(r"(?<=[A-Z])([A-Z][a-z])", r"_\1", name).lower()


def raw(type_: str, *keys: str, name: str | None = None) -> Column:
    """Column copied from the raw object at `keys` (named after the last key unless `name`)."""
    return Column(name or snake(keys[-1]), type_, keys)


def computed(type_: str, name: str) -> Column:
    return Column(name, type_)


def _many(type_: str, keys: str) -> list[Column]:
    return [raw(type_, key) for key in keys.split()]


# ----------------------------------------------------------------- matches

MATCHES = Table(
    "matches",
    (
        computed("str", "match_id"),
        computed("str", "platform_id"),
        computed("str", "patch"),
        raw("str", "gameVersion"),
        computed("str", "data_version"),
        raw("int", "gameId"),
        raw("int", "queueId"),
        raw("int", "mapId"),
        raw("str", "gameMode"),
        raw("str", "gameType"),
        computed("ts", "game_creation"),
        computed("ts", "game_start"),
        computed("ts", "game_end"),
        raw("int", "gameDuration", name="duration_s"),
        computed("int", "winning_team"),
        raw("str", "endOfGameResult"),
        computed("bool", "surrender"),
        computed("bool", "early_surrender"),
        computed("str", "seed_tier"),
        computed("str", "seed_puuid"),
        computed("str", "strategy"),
        computed("ts", "fetched_at"),
    ),
)

# ----------------------------------------------------------------- participants

_PARTICIPANT_INTS = """
    participantId teamId championId champLevel champExperience
    kills deaths assists goldEarned goldSpent totalMinionsKilled neutralMinionsKilled
    totalAllyJungleMinionsKilled totalEnemyJungleMinionsKilled
    totalDamageDealt totalDamageDealtToChampions physicalDamageDealtToChampions
    magicDamageDealtToChampions trueDamageDealtToChampions
    totalDamageTaken physicalDamageTaken magicDamageTaken trueDamageTaken damageSelfMitigated
    damageDealtToBuildings damageDealtToObjectives damageDealtToTurrets damageDealtToEpicMonsters
    totalHeal totalHealsOnTeammates totalDamageShieldedOnTeammates timeCCingOthers totalTimeCCDealt
    visionScore wardsPlaced wardsKilled visionWardsBoughtInGame detectorWardsPlaced
    item0 item1 item2 item3 item4 item5 item6 roleBoundItem
    summoner1Id summoner2Id summoner1Casts summoner2Casts
    spell1Casts spell2Casts spell3Casts spell4Casts
    doubleKills tripleKills quadraKills pentaKills largestMultiKill largestKillingSpree killingSprees
    turretKills turretTakedowns inhibitorKills inhibitorTakedowns baronKills dragonKills
    objectivesStolen objectivesStolenAssists
    timePlayed totalTimeSpentDead longestTimeSpentLiving summonerLevel profileIcon
"""
_PARTICIPANT_BOOLS = """
    win firstBloodKill firstBloodAssist firstTowerKill firstTowerAssist
    gameEndedInSurrender gameEndedInEarlySurrender teamEarlySurrendered
"""
_PARTICIPANT_STRS = "championName teamPosition individualPosition lane role riotIdGameName riotIdTagline"
_CHALLENGES = """
    kda killParticipation teamDamagePercentage damageTakenOnTeamPercentage damagePerMinute goldPerMinute
    visionScorePerMinute soloKills skillshotsHit skillshotsDodged laneMinionsFirst10Minutes
    jungleCsBefore10Minutes maxCsAdvantageOnLaneOpponent laningPhaseGoldExpAdvantage
    earlyLaningPhaseGoldExpAdvantage turretPlatesTaken controlWardsPlaced takedowns
"""

PARTICIPANTS = Table(
    "participants",
    (
        computed("str", "match_id"),
        computed("str", "platform_id"),
        computed("str", "patch"),
        raw("str", "puuid"),
        *_many("int", _PARTICIPANT_INTS),
        *_many("bool", _PARTICIPANT_BOOLS),
        *_many("str", _PARTICIPANT_STRS),
        *[raw("float", "challenges", key, name=f"ch_{snake(key)}") for key in _CHALLENGES.split()],
        computed("int", "primary_style"),
        computed("int", "sub_style"),
        *[computed("int", f"perk{i}") for i in range(6)],
        raw("int", "perks", "statPerks", "offense", name="stat_perk_offense"),
        raw("int", "perks", "statPerks", "flex", name="stat_perk_flex"),
        raw("int", "perks", "statPerks", "defense", name="stat_perk_defense"),
        computed("str", "rank_status"),
        computed("str", "tier"),
        computed("str", "division"),
        computed("int", "lp"),
        computed("int", "rank_wins"),
        computed("int", "rank_losses"),
        computed("ts", "rank_fetched_at"),
        computed("float", "rank_lag_hours"),
    ),
)

# ----------------------------------------------------------------- teams

TEAM_OBJECTIVES = ("baron", "champion", "dragon", "horde", "inhibitor", "riftHerald", "tower", "atakhan", "voidGator")

TEAMS = Table(
    "teams",
    (
        computed("str", "match_id"),
        computed("str", "platform_id"),
        computed("str", "patch"),
        raw("int", "teamId"),
        raw("bool", "win"),
        computed("list_int", "bans"),
        *[
            column
            for objective in TEAM_OBJECTIVES
            for column in (
                raw("bool", "objectives", objective, "first", name=f"{snake(objective)}_first"),
                raw("int", "objectives", objective, "kills", name=f"{snake(objective)}_kills"),
            )
        ],
    ),
)

# ----------------------------------------------------------------- timelines

_FRAME_INTS = """
    participantId currentGold totalGold goldPerSecond xp level minionsKilled jungleMinionsKilled
    timeEnemySpentControlled
"""
_CHAMPION_STATS = """
    abilityHaste abilityPower armor armorPen armorPenPercent attackDamage attackSpeed bonusArmorPenPercent
    bonusMagicPenPercent ccReduction cooldownReduction health healthMax healthRegen lifesteal magicPen
    magicPenPercent magicResist movementSpeed omnivamp physicalVamp power powerMax powerRegen spellVamp
"""
_DAMAGE_STATS = """
    magicDamageDone magicDamageDoneToChampions magicDamageTaken physicalDamageDone
    physicalDamageDoneToChampions physicalDamageTaken totalDamageDone totalDamageDoneToChampions
    totalDamageTaken trueDamageDone trueDamageDoneToChampions trueDamageTaken
"""

TIMELINE_PARTICIPANT_FRAMES = Table(
    "timeline_participant_frames",
    (
        computed("str", "match_id"),
        computed("str", "platform_id"),
        computed("str", "patch"),
        computed("int", "minute"),
        computed("int", "timestamp_ms"),
        computed("str", "puuid"),
        computed("int", "team_id"),
        *_many("int", _FRAME_INTS),
        raw("int", "position", "x", name="x"),
        raw("int", "position", "y", name="y"),
        *[raw("int", "championStats", key) for key in _CHAMPION_STATS.split()],
        *[raw("int", "damageStats", key) for key in _DAMAGE_STATS.split()],
    ),
)

_EVENT_INTS = """
    realTimestamp participantId killerId victimId killerTeamId teamId creatorId itemId afterId beforeId
    goldGain skillSlot level multiKillLength killStreakLength bounty shutdownBounty winningTeam actualStartTime
"""
_EVENT_STRS = "levelUpType wardType buildingType towerType laneType monsterType monsterSubType killType name"

TIMELINE_EVENTS = Table(
    "timeline_events",
    (
        computed("str", "match_id"),
        computed("str", "platform_id"),
        computed("str", "patch"),
        computed("int", "event_index"),
        computed("int", "minute"),
        raw("int", "timestamp", name="timestamp_ms"),
        raw("str", "type"),
        *_many("int", _EVENT_INTS),
        *_many("str", _EVENT_STRS),
        computed("list_int", "assisting_participant_ids"),
        raw("int", "position", "x", name="x"),
        raw("int", "position", "y", name="y"),
    ),
)

# ----------------------------------------------------------------- ranks

RANKS = Table(
    "ranks",
    (
        computed("str", "puuid"),
        computed("str", "platform_id"),
        computed("ts", "fetched_at"),
        computed("str", "rank_status"),
        raw("str", "tier"),
        raw("str", "rank", name="division"),
        raw("int", "leaguePoints", name="lp"),
        raw("int", "wins"),
        raw("int", "losses"),
        raw("bool", "hotStreak"),
        raw("bool", "veteran"),
        raw("bool", "freshBlood"),
        raw("bool", "inactive"),
    ),
)

# ----------------------------------------------------------------- Data Dragon dimensions

_CHAMPION_DIM_STATS = """
    hp hpperlevel mp mpperlevel movespeed armor armorperlevel spellblock spellblockperlevel attackrange
    hpregen hpregenperlevel mpregen mpregenperlevel crit critperlevel attackdamage attackdamageperlevel
    attackspeedperlevel attackspeed
"""

DIM_CHAMPIONS = Table(
    "dim_champions",
    (
        computed("str", "ddragon_version"),
        computed("str", "patch"),
        computed("int", "champion_id"),
        raw("str", "id", name="champion_key"),
        raw("str", "name"),
        raw("str", "title"),
        raw("list_str", "tags"),
        raw("str", "partype"),
        *[raw("int", "info", key, name=f"info_{key}") for key in ("attack", "defense", "magic", "difficulty")],
        *[raw("float", "stats", key, name=f"stat_{key}") for key in _CHAMPION_DIM_STATS.split()],
    ),
    partitioned=False,
)

DIM_ITEMS = Table(
    "dim_items",
    (
        computed("str", "ddragon_version"),
        computed("str", "patch"),
        computed("int", "item_id"),
        raw("str", "name"),
        raw("str", "plaintext"),
        raw("int", "gold", "base", name="gold_base"),
        raw("int", "gold", "total", name="gold_total"),
        raw("int", "gold", "sell", name="gold_sell"),
        raw("bool", "gold", "purchasable", name="purchasable"),
        raw("list_str", "tags"),
        raw("int", "depth"),
        computed("list_int", "from_items"),
        computed("list_int", "into_items"),
        raw("bool", "maps", "11", name="on_summoners_rift"),
        computed("str", "stats_json"),
    ),
    partitioned=False,
)

DIM_SUMMONER_SPELLS = Table(
    "dim_summoner_spells",
    (
        computed("str", "ddragon_version"),
        computed("str", "patch"),
        computed("int", "spell_id"),
        raw("str", "id", name="spell_key"),
        raw("str", "name"),
        computed("float", "cooldown"),
        raw("int", "summonerLevel"),
        raw("list_str", "modes"),
    ),
    partitioned=False,
)

DIM_RUNES = Table(
    "dim_runes",
    (
        computed("str", "ddragon_version"),
        computed("str", "patch"),
        computed("int", "rune_id"),
        computed("str", "rune_key"),
        computed("str", "name"),
        computed("int", "style_id"),
        computed("str", "style_name"),
        computed("int", "slot"),
    ),
    partitioned=False,
)

TABLES: dict[str, Table] = {
    table.name: table
    for table in (
        MATCHES,
        PARTICIPANTS,
        TEAMS,
        TIMELINE_PARTICIPANT_FRAMES,
        TIMELINE_EVENTS,
        RANKS,
        DIM_CHAMPIONS,
        DIM_ITEMS,
        DIM_SUMMONER_SPELLS,
        DIM_RUNES,
    )
}


def _check_unique_names() -> None:
    for table in TABLES.values():
        names = table.column_names
        duplicates = {n for n in names if names.count(n) > 1}
        if duplicates:
            raise AssertionError(f"{table.name}: duplicate columns {sorted(duplicates)}")


_check_unique_names()
