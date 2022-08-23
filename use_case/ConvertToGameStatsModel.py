from typing import Dict, Any, List
from model.GameStatsModel import ParticipantInfo, Info, TeamInfo, GameStatsModel
from model.TeamPositionEnum import TeamPosition


class DataRetriever:

    @staticmethod
    def __determineTier(playerPuuid: str, playersTierMapper: Dict[str, str]) -> str:
        if len(playersTierMapper) == 0:
            return "UNSPECIFIED"
        else:
            return playersTierMapper[playerPuuid]

    @staticmethod
    def __determineTeamPosition(position: str) -> str:
        positionType = TeamPosition.TOP.name
        for teamPosition in TeamPosition:
            if teamPosition.name == position:
                positionType = teamPosition.name
                break
        return positionType

    @staticmethod
    def __createParticipantInfo(player: Dict[str, Any], playersTierMapper: Dict[str, str]) -> ParticipantInfo:
        return ParticipantInfo(
            assists=player["assists"],
            deaths=player["deaths"],
            kills=player["kills"],
            goldEarned=player["goldEarned"],
            magicDamageDealtToChampions=player["magicDamageDealtToChampions"],
            physicalDamageDealtToChampions=player["physicalDamageDealtToChampions"],
            teamPosition=DataRetriever.__determineTeamPosition(player["teamPosition"]),
            totalDamageDealtToChampions=player["totalDamageDealtToChampions"],
            trueDamageDealtToChampions=player["trueDamageDealtToChampions"],
            visionScore=player["visionScore"],
            wardsKilled=player["wardsKilled"],
            wardsPlaced=player["wardsPlaced"],
            visionWardsBoughtInGame=player["visionWardsBoughtInGame"],
            totalMinionsKilled=player["totalMinionsKilled"],
            teamId=player["teamId"],
            tier=DataRetriever.__determineTier(player["puuid"], playersTierMapper)
        )

    @staticmethod
    def createParticipantsInfoList(listOfPlayers: List[Dict[str, Any]], playersTierMapper: Dict[str, str]) -> List[ParticipantInfo]:
        participantsList: List[ParticipantInfo] = []
        for player in listOfPlayers:
            participantsList.append(DataRetriever.__createParticipantInfo(player, playersTierMapper))

        return participantsList

    @staticmethod
    def createInfo(gameDuration: int, participants: List[ParticipantInfo]) -> Info:
        return Info(gameDuration=gameDuration, participants=participants)

    @staticmethod
    def createTeamInfo(teamInfoList: List[Dict[str, Any]]) -> List[TeamInfo]:
        listTeamInfo: List[TeamInfo] = []
        for team in teamInfoList:
            listTeamInfo.append(
                TeamInfo(kills=team["objectives"]["champion"]["kills"], teamId=team["teamId"], win=team["win"])
            )

        return listTeamInfo

    @staticmethod
    def createGameStatsModel(participants: List[str], info: Info, teamsInfo: List[TeamInfo]) -> GameStatsModel:
        return GameStatsModel(participants=participants, info=info, teamsInfo=teamsInfo)


def ConvertToGameStatsModel(responseObject: Dict[str, Any], playersTierMapper: Dict[str, str]) -> GameStatsModel:
    participantsInfoListDto: List[Dict[str, Any]] = responseObject["info"]["participants"]
    participantsInfoList = DataRetriever.createParticipantsInfoList(participantsInfoListDto, playersTierMapper)

    infoObject = DataRetriever.createInfo(responseObject["info"]["gameDuration"], participantsInfoList)

    teamInfoDto: List[Dict[str, Any]] = responseObject["info"]["teams"]
    teamInfoList = DataRetriever.createTeamInfo(teamInfoList=teamInfoDto)

    gameStatsModel = DataRetriever.createGameStatsModel(participants=responseObject["metadata"]["participants"],
                                                        info=infoObject, teamsInfo=teamInfoList)

    return gameStatsModel
