import requests as r
from typing import List, Any, Dict
from utils.DateFormatter import DateFormatter
from .utils.RiotApiRequestHandler import RiotApiRequestHandler
import constants.Config as Config
from model.SummonerInfoModel import SummonerInfoModel


class LeagueApiController:
    """
    Interface to make Riot API calls.
    """

    __API_KEY: str

    def __init__(self):
        self.__API_KEY = Config.RIOT_API

    def getSummonerInfo(self, summonerName: str) -> Dict[str, Any]:
        """
        Make get request for /lol/summoner/v4/summoner/by-name/[summonerName] and returns information for given summoner.
        
        :param summonerName: Name of the summoner to search for.
        :return: Information of summoner.
        """
        summonerInfoResponse: r.Response = RiotApiRequestHandler.get(
            url=f"https://eun1.api.riotgames.com/lol/summoner/v4/summoners/by-name/{summonerName}",
            apiKey=self.__API_KEY
        )
        summonerInfo: SummonerInfoModel = summonerInfoResponse.json()
        return summonerInfo

    def getMatchesFromPuuid(self, puuid: str, startDate: str = DateFormatter.getLastMonthInSec(), count: int = 100) -> List[str]:
        """
        Make get request for /lol/match/v5/matches/by-puuid/[puuid]/ids and returns list of codes for given puuid.

        :param puuid: Individual id of the summoner.
        :param startDate: Date from which search will begin. Default value is from 4 month prior to today.
        :param count: Amount of match codes to get. Default value is 100.
        :return: List of codes for matches. Codes are string.
        """
        endDate: str = DateFormatter.getTodayInSec()

        listOfCodesResponse: r.Response = RiotApiRequestHandler.get(
            url=f"https://europe.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids",
            apiKey=self.__API_KEY,
            params={
                "startTime": startDate,
                "endTime": endDate,
                "type": "ranked",
                "start": "0",
                "count": count
            }
        )
        listOfCodes: List[str] = listOfCodesResponse.json()
        return listOfCodes

    def getMatchInfoFromCode(self, matchCode: str) -> Dict[str, Any]:
        """
        Make get request for /lol/match/v5/matches/[matchCode] to get information for given match code.

        :param matchCode: Code of match. Contains indicator of the region, followed by underscore than by numeric ID.
        :return: Object of a match information.
        """

        response: r.Response = RiotApiRequestHandler.get(
            url=f"https://europe.api.riotgames.com/lol/match/v5/matches/{matchCode}",
            apiKey=self.__API_KEY
        )
        # TODO: zmienić nazwę obiektu dla riotu
        bullshitObject = response.json()
        return bullshitObject
