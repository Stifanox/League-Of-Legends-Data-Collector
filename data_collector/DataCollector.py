import random

from remote.LeagueApiController import LeagueApiController
from typing import Dict, Any, Set, List
from use_case.ConvertToGameStatsModel import ConvertToGameStatsModel
from model.GameStatsModel import GameStatsModel
from use_case.ConvertToSummonerInfoModel import ConvertToSummonerInfoModel
from .MatchCodesSaver import MatchCodesSaver
from .MatchDataSaver import MatchDataSaver
from use_case.GetPlayersTier import GetPlayersTier


class DataCollector:
    """
    Class responsible for collecting and saving data from matches. The class is collecting data until cycle counter is 0.
    """

    __apiController: LeagueApiController
    __codeUsed: Set[str]
    __matchCodesSaver: MatchCodesSaver
    __matchDataSaver: MatchDataSaver
    __lastPuuid: str
    __withTier: bool
    __count: int

    def __init__(self, cycles: int, withTier: bool, count: int = 100):
        self.__apiController: LeagueApiController = LeagueApiController()
        self.__matchCodesSaver: MatchCodesSaver = MatchCodesSaver()
        self.__matchDataSaver: MatchDataSaver = MatchDataSaver()
        self.__codesUsed: Set[str] = self.__matchCodesSaver.importCodes()
        self.__lastPuuid = ""
        self.__maxCycle = cycles
        self.__withTier = withTier
        self.__count = count

    def startCollector(self, summonerName: str):
        """
        Initializes collecting data.

        :param summonerName: Summoner name to start collecting data from.
        """
        print(f"Starting collecting data, amount cycle left: {self.__maxCycle}")
        summonerInfoDto: Dict[str, Any] = self.__apiController.getSummonerInfoByName(summonerName)
        summonerInfo = ConvertToSummonerInfoModel(summonerInfoDto)
        codes = self.__apiController.getMatchCodesFromPuuid(summonerInfo.puuid, count=self.__count)
        self.__cycleAllCodes(codes)

    def __cycleAllCodes(self, listOfCodes: Set[str]):
        """
        Cycles through all codes given then creating a list of :class: `GameStatsModel`.
        After that update the code list that was used to get data and saves them to file.

        :param listOfCodes: List of codes to get data from
        """
        allData: List[GameStatsModel] = []
        for code in listOfCodes:
            print(f"Code getting info from: {code}")
            # If codes was already used to collect data from than skips the code and goes to another
            if code in self.__codesUsed:
                continue
            matchData = self.__getStatsFromMatches(code)
            allData.append(matchData)
        # Do an update on codes that was used to collect data
        self.__codesUsed.update(listOfCodes)
        self.__saveMatchDataToFile(allData)
        self.__saveCodesToFile()
        self.__chooseNewPlayer(allData[0].participants)

    def __getStatsFromMatches(self, code: str) -> GameStatsModel:
        """
        Fetches data from api then creating :class: `GameStatsModel`.

        :param code: Single match code
        :return: :class: `GameStatsModel` object
        """
        matchInfo: Dict[str, Any] = self.__apiController.getMatchInfoFromCode(code)
        playersTierMapper = dict()
        if self.__withTier:
            playersTierMapper: Dict[str, str] = GetPlayersTier(matchInfo["metadata"]["participants"])
        usefulInfo: GameStatsModel = ConvertToGameStatsModel(matchInfo, playersTierMapper)
        return usefulInfo

    def __chooseNewPlayer(self, listOfParticipants: List[str]):
        """
        Chooses a new player to get matches from. Player is chosen randomly but can't be the same player as the previous from which matches was collected.

        :param listOfParticipants:
        """
        self.__maxCycle -= 1
        print(f"Amount cycle remaining: {self.__maxCycle}")

        if self.__maxCycle == 0:
            return

        playerChosen = listOfParticipants[random.randint(0, len(listOfParticipants)-1)]
        while playerChosen == self.__lastPuuid:
            playerChosen = listOfParticipants[random.randint(0, len(listOfParticipants) - 1)]
        print(f"Player chosen: {playerChosen}")
        self.__lastPuuid = playerChosen
        codes = self.__apiController.getMatchCodesFromPuuid(playerChosen, count=self.__count)
        self.__cycleAllCodes(codes)

    def __saveCodesToFile(self):
        """
        Saves all codes used to collect data to avoid duplicates matches.
        """
        self.__matchCodesSaver.saveCodes(self.__codesUsed)

    def __saveMatchDataToFile(self, gameData: List[GameStatsModel]):
        self.__matchDataSaver.saveData(gameData)
