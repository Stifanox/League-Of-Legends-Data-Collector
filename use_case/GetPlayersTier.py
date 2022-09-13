import time
from typing import List, Dict
from remote.LeagueApiController import LeagueApiController
from use_case.ConvertToSummonerInfoModel import ConvertToSummonerInfoModel
from model.SummonerInfoModel import SummonerInfoModel
from alive_progress import alive_bar


def GetPlayersTier(playerList: List[str]) -> Dict[str, str]:
    """
    Gets players tier from puuid.

    :param playerList: List of players puuid.
    :return: Dictionary mapped as: {puuid: tier}.
    """
    apiController = LeagueApiController()
    listOfTiers = dict()
    with alive_bar(10) as bar:
        for i, playerId in enumerate(playerList):

            summonerInfoDto = apiController.getSummonerInfoByPuuid(playerId)
            summonerInfo: SummonerInfoModel = ConvertToSummonerInfoModel(summonerInfoDto)
            tierInfo = apiController.getPlayerTierFromId(summonerInfo.id)

            try:
                listOfTiers[summonerInfo.puuid] = tierInfo["tier"]
            except KeyError:
                print(f"Player doesn't have tier in solo queue: {summonerInfo.name}")
                listOfTiers[summonerInfo.puuid] = "UNSPECIFIED"
            bar()
    return listOfTiers
