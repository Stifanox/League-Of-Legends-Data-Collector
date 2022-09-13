from data_collector.DataCollector import DataCollector

if __name__ == '__main__':
    collector = DataCollector(cycles=15, withTier=True, count=20)

    collector.startCollector("hate")
