from typing import List, Dict
from remote.LeagueApiController import LeagueApiController
from use_case.ConvertToSummonerInfoModel import ConvertToSummonerInfoModel
from model.SummonerInfoModel import SummonerInfoModel


def GetPlayersTier(playerList: List[str]) -> Dict[str, str]:
    apiController = LeagueApiController()
    listOfTiers = dict()
    for playerId in playerList:
        summonerInfoDto = apiController.getSummonerInfoByPuuid(playerId)
        summonerInfo: SummonerInfoModel = ConvertToSummonerInfoModel(summonerInfoDto)
        tierInfo = apiController.getPlayerTierFromId(summonerInfo.id)
        # There is some weird bug where you need to specify key to variable ¯\_(ツ)_/¯
        listOfTiers[summonerInfo.puuid] = tierInfo["tier"]
    return listOfTiers
