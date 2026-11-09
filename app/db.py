"""Supabase DB 읽기. 서버는 읽기만 한다 (user.password는 절대 조회하지 않는다).

테이블
- info:     id(-> user.id), age, height, weight
- schedule: author(-> user.id), id, start_term, end_term (timestamptz), name

연결 확인:  python -m app.db <user_id>
"""

import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache

from supabase import Client, create_client

from app.config import SUPABASE_KEY, SUPABASE_URL, TIMEZONE


@dataclass
class ScheduleEvent:
    start: datetime      # TIMEZONE(한국 시간) 기준
    end: datetime
    name: str | None


@lru_cache(maxsize=1)
def get_client() -> Client:
    """처음 쓸 때 한 번만 연결한다. 키가 없어도 서버는 뜨고, DB를 쓰는 순간 오류가 난다."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(".env에 SUPABASE_URL, SUPABASE_KEY가 없습니다.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_user_info(user_id: int) -> dict | None:
    """info 테이블의 {"age", "height", "weight"}. 없으면 None."""
    rows = (
        get_client().table("info")
        .select("age, height, weight")
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def fetch_schedules(user_id: int, start: date, end: date) -> list[ScheduleEvent]:
    """start~end(포함) 기간과 겹치는 사용자의 일정. 시작 시각 순."""
    range_start = datetime.combine(start, time.min, TIMEZONE)
    range_end = datetime.combine(end + timedelta(days=1), time.min, TIMEZONE)
    rows = (
        get_client().table("schedule")
        .select("start_term, end_term, name")
        .eq("author", user_id)
        .lt("start_term", range_end.isoformat())     # 기간 끝 전에 시작하고
        .gt("end_term", range_start.isoformat())     # 기간 시작 후에 끝나는 일정
        .order("start_term")
        .execute()
        .data
    )
    return [
        ScheduleEvent(
            start=datetime.fromisoformat(r["start_term"]).astimezone(TIMEZONE),
            end=datetime.fromisoformat(r["end_term"]).astimezone(TIMEZONE),
            name=r["name"],
        )
        for r in rows
    ]


if __name__ == "__main__":
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print("info:", fetch_user_info(uid))
    today = date.today()
    for e in fetch_schedules(uid, today, today + timedelta(days=13)):
        print("schedule:", e.start.strftime("%Y-%m-%d %H:%M"), "~", e.end.strftime("%H:%M"), e.name)
