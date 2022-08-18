from typing import Dict, Any, List
from model.GameStatsModel import ParticipantInfo, Info, TeamInfo, GameStatsModel
from model.TeamPositionModel import TeamPosition


class DataRetriever:

    @staticmethod
    def __determineTeamPosition(position: str) -> TeamPosition:
        positionType = TeamPosition.TOP
        for teamPosition in TeamPosition:
            if teamPosition.name == position:
                positionType = teamPosition
                break
        return positionType

    @staticmethod
    def __createParticipantInfo(player: Dict[str, Any]) -> ParticipantInfo:
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
            teamId=player["teamId"]
        )

    @staticmethod
    def createParticipantsInfoList(listOfPlayers: List[Dict[str, Any]]) -> List[ParticipantInfo]:
        participantsList: List[ParticipantInfo] = []
        for player in listOfPlayers:
            participantsList.append(DataRetriever.__createParticipantInfo(player))

        return participantsList

    @staticmethod
    def createInfo(gameDuration: int, participants: List[ParticipantInfo]) -> Info:
        return Info(gameDuration=gameDuration, participants=participants)

    @staticmethod
    def createTeamInfo(teamInfoList: List[Dict[str, Any]]) -> List[TeamInfo]:
        listTeamInfo: List[TeamInfo] = []
        for team in teamInfoList:
            listTeamInfo.append(
                TeamInfo(kills=team["objectives"]["champion"]["kills"], teamId=team["teamId"], win=team["win"]))

        return listTeamInfo

    @staticmethod
    def createGameStatsModel(participants: List[str], info: Info, teamsInfo: List[TeamInfo]) -> GameStatsModel:
        return GameStatsModel(participants=participants, info=info, teamsInfo=teamsInfo)


def ConvertToGameStatsModel(responseObject: Dict[str, Any]) -> GameStatsModel:
    participantsInfoListDto: List[Dict[str, Any]] = responseObject["info"]["participants"]
    participantsInfoList = DataRetriever.createParticipantsInfoList(participantsInfoListDto)

    infoObject = DataRetriever.createInfo(responseObject["info"]["gameDuration"], participantsInfoList)

    teamInfoDto: List[Dict[str, Any]] = responseObject["info"]["teams"]
    teamInfoList = DataRetriever.createTeamInfo(teamInfoList=teamInfoDto)

    gameStatsModel = DataRetriever.createGameStatsModel(participants=responseObject["metadata"]["participants"],
                                                        info=infoObject, teamsInfo=teamInfoList)

    return gameStatsModel
