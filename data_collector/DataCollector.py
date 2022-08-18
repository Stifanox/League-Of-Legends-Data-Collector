from remote.LeagueApiController import LeagueApiController
from typing import List, Dict, Any
from use_case.ConvertToGameStatsModel import ConvertToGameStatsModel
from model.GameStatsModel import GameStatsModel
from use_case.ConvertToSummonerInfoModel import ConvertToSummonerInfoModel


# TODO: dodać dokumentację
class DataCollector:
    __apiController: LeagueApiController

    def __init__(self):
        self.__apiController: LeagueApiController = LeagueApiController()

    def startCollector(self, summonerName: str):
        summonerInfoDto: Dict[str, Any] = self.__apiController.getSummonerInfo(summonerName)
        summonerInfo = ConvertToSummonerInfoModel(summonerInfoDto)
        codes = self.__apiController.getMatchesFromPuuid(summonerInfo.puuid)
        self.__getStatsFromMatches(codes)

    def __getStatsFromMatches(self, listOfCodes: List[str]):
        matchInfo: Dict[str, Any] = self.__apiController.getMatchInfoFromCode(listOfCodes[0])
        usefulInfo: GameStatsModel = ConvertToGameStatsModel(matchInfo)

