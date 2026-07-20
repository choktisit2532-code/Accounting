from datetime import date, datetime
from zoneinfo import ZoneInfo


BANGKOK_TIMEZONE = ZoneInfo("Asia/Bangkok")


def bangkok_now() -> datetime:
    return datetime.now(BANGKOK_TIMEZONE)


def bangkok_today() -> date:
    return bangkok_now().date()
