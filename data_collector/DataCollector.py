from remote.LeagueApiController import LeagueApiController
from typing import Dict, Any, Set
from use_case.ConvertToGameStatsModel import ConvertToGameStatsModel
from model.GameStatsModel import GameStatsModel
from use_case.ConvertToSummonerInfoModel import ConvertToSummonerInfoModel
from .MatchCodesHandler import MatchCodesHandler


# TODO: dodać dokumentację
class DataCollector:
    """
    Class responsible for collecting and saving data from matches. The class is collecting data until [to be specified xd]
    """

    __apiController: LeagueApiController
    __codeUsed: Set[str]
    __matchCodesSaver: MatchCodesHandler

    def __init__(self):
        self.__apiController: LeagueApiController = LeagueApiController()
        self.__matchCodesSaver: MatchCodesHandler = MatchCodesHandler()
        self.__codesUsed: Set[str] = self.__matchCodesSaver.importCodes()

    def startCollector(self, summonerName: str):
        """
        Initializes collecting data.

        :param summonerName: Summoner name to start collecting data from.
        """
        summonerInfoDto: Dict[str, Any] = self.__apiController.getSummonerInfo(summonerName)
        summonerInfo = ConvertToSummonerInfoModel(summonerInfoDto)
        codes = self.__apiController.getMatchCodesFromPuuid(summonerInfo.puuid)
        self.__cycleAllCodes(codes)

    def __cycleAllCodes(self, listOfCodes: Set[str]):
        """
        Cycles through all codes given then creating a list of :class: `GameStatsModel`.
        After that update the code list that was used to get data and saves them to file.

        :param listOfCodes: List of codes to get data from
        """
        allData = []
        for code in listOfCodes:
            # If codes was already used to collect data from than skips the code and goes to another
            if code in self.__codesUsed:
                continue
            matchData = self.__getStatsFromMatches(code)
            allData.append(matchData)
        # Do an update on codes that was used to collect data
        self.__codesUsed.update(listOfCodes)
        self.__saveCodesToFile()

    def saveMatchDataToFile(self):
        pass

    def __getStatsFromMatches(self, code: str) -> GameStatsModel:
        """
        Fetches data from api then creating :class: `GameStatsModel`.

        :param code: Single match code
        :return: :class: `GameStatsModel` object
        """
        matchInfo: Dict[str, Any] = self.__apiController.getMatchInfoFromCode(code)
        usefulInfo: GameStatsModel = ConvertToGameStatsModel(matchInfo)
        return usefulInfo

    def __saveCodesToFile(self):
        """
        Saves all codes used to collect data to avoid duplicates matches.
        """
        self.__matchCodesSaver.saveCodes(self.__codesUsed)
