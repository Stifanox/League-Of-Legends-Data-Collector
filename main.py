from data_collector.DataCollector import DataCollector
from data_collector.MatchCodesSaver import MatchCodesSaver
from remote.LeagueApiController import LeagueApiController


if __name__ == '__main__':
    saver = MatchCodesSaver()
    controller = LeagueApiController()
    collector = DataCollector(cycles=10, withTier=False)

    collector.startCollector("stifano")


