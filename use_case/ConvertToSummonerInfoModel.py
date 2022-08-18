from model.SummonerInfoModel import SummonerInfoModel
from typing import Dict, Any


def ConvertToSummonerInfoModel(responseObject: Dict[str, Any]) -> SummonerInfoModel:
    return SummonerInfoModel(
        id=responseObject["id"],
        profileIconId=responseObject["profileIconId"],
        revisionDate=responseObject["revisionDate"],
        accountId=responseObject["accountId"],
        name=responseObject["name"],
        puuid=responseObject["puuid"],
        summonerLevel=responseObject["summonerLevel"]
    )
