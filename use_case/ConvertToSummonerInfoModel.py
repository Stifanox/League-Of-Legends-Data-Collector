from model.SummonerInfoModel import SummonerInfoModel
from typing import Dict, Any


def ConvertToSummonerInfoModel(responseObject: Dict[str, Any]) -> SummonerInfoModel:
    """
    Convert object from Riot API response to dataclass.

    :param responseObject: Response object from Riot API from summoner-v4.
    :return: SummonerInfoModel
    """
    return SummonerInfoModel(
        id=responseObject["id"],
        profileIconId=responseObject["profileIconId"],
        revisionDate=responseObject["revisionDate"],
        accountId=responseObject["accountId"],
        name=responseObject["name"],
        puuid=responseObject["puuid"],
        summonerLevel=responseObject["summonerLevel"]
    )
