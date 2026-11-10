# 주(월~일) 계산. "오늘"은 서버 시간대와 관계없이 한국 시간 기준이다 (배포 서버는 보통 UTC).

from datetime import date, datetime, timedelta

from app.config import TIMEZONE

WEEKDAY_NAMES = "월화수목금토일"   # 0=월 ... 6=일 (date.weekday()와 같은 순서)


def today() -> date:
    return datetime.now(TIMEZONE).date()


def week_bounds(day: date) -> tuple[date, date]:
    # day가 속한 주의 (월요일, 일요일)
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def days_between(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
