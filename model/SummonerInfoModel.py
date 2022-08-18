from dataclasses import dataclass


@dataclass
class SummonerInfoModel:
    accountId: str
    profileIconId: int
    revisionDate: int
    name: str
    id: str
    puuid: str
    summonerLevel: int
