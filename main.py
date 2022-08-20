import dataclasses

from model.GameStatsModel import GameStatsModel
from data_collector.DataCollector import DataCollector
from data_collector.MatchCodesHandler import MatchCodesHandler
from remote.LeagueApiController import LeagueApiController

# TODO: zrobić dekorator dla dataclass
if __name__ == '__main__':
    saver = MatchCodesHandler()
    controller = LeagueApiController()
    collector = DataCollector()

    collector.startCollector("stifano")


