import datetime
import time


class DateFormatter:
    @staticmethod
    def getLastMonthInSec() -> str:
        today = datetime.datetime.today()
        offset = today - datetime.timedelta(days=120)
        day = offset.strftime("%d")
        month = offset.strftime("%m")
        year = offset.strftime("%Y")
        lastMonthInSec = DateFormatter.__convertFloatToString(datetime.datetime(year=int(year), month=int(month), day=int(day)).timestamp())
        return lastMonthInSec

    @staticmethod
    def getTodayInSec() -> str:
        return DateFormatter.__convertFloatToString(time.time())

    @staticmethod
    def __convertFloatToString(number: float) -> str:
        return str(int(number))
