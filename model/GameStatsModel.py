from dataclasses import dataclass
from typing import List
from model.TeamPositionModel import TeamPosition


@dataclass
class ParticipantInfo:
    """
    Contains info about one player of the match

    :param assists: Player assists.
    :param deaths: Player deaths.
    :param kills: Player kills.
    :param goldEarned: Player gold earn from all resources.
    :param magicDamageDealtToChampion: Player magic damage dealt to enemies.
    :param physicalDamageDealtToChampions: Player physical damage dealt to enemies.
    :param teamPosition: Player position guess based on assumption.
    :param totalDamageDealtToChampions: Player total damage dealt to enemies.
    :param trueDamageDealtToChampions: Player true damage dealt to enemies.
    :param visionScore: Player vision score.
    :param wardsKilled: How many wards was destroyed by the player.
    :param wardsPlaced: How many wards was placed by the player.
    :param visionWardsBoughtInGame: How many vision wards was bought by the player. Do not confuse with sight wards (old wards, removed in patch v6.22)
    :param totalMinionsKilled: How much creep score player have
    :param teamId: Player's team id to identify whether he won or lost
    """
    assists: int
    deaths: int
    kills: int
    goldEarned: int
    magicDamageDealtToChampions: int
    physicalDamageDealtToChampions: int
    teamPosition: TeamPosition
    totalDamageDealtToChampions: int
    trueDamageDealtToChampions: int
    visionScore: int
    wardsKilled: int
    wardsPlaced: int
    visionWardsBoughtInGame: int
    totalMinionsKilled: int
    teamId: int


@dataclass
class Info:
    """
    Contains info about players and how long the game was.

    :param gameDuration: Describe how long the match was played. Value is represented by seconds.
    :param participants: List of player that played the game.
    """
    gameDuration: int
    participants: List[ParticipantInfo]


@dataclass
class TeamInfo:
    """
    Contains info about all kills and additional info whether team won or lost
    """
    kills: int
    teamId: int
    win: bool


@dataclass
class GameStatsModel:
    """
    Main model to use when fetching info from /lol/match/v5/matches/[matchId]

    :param participants: List of players puuid.
    :param info: Info dataclass
    :param teamsInfo: List of TeamInfo
    """
    participants: List[str]
    info: Info
    teamsInfo: List[TeamInfo]
