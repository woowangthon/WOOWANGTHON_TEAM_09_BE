"""Supabase DB 접근.

user.password(해시)는 로그인 확인(fetch_login)에서만 읽고, 다른 곳에서는 조회하지 않는다.

테이블
- user:     id(내부 식별자), created_at, name(로그인 아이디), password(Argon2 해시)
- info:     id(-> user.id), age, height, weight
- schedule: author(-> user.id), id, start_term, end_term (timestamptz), name

연결 확인:  python -m app.db <user_id>
"""

import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import lru_cache

from postgrest.exceptions import APIError
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


class NameTakenError(Exception):
    """같은 name(로그인 아이디)의 사용자가 이미 있음."""


UNIQUE_VIOLATION = "23505"   # PostgreSQL 오류 코드


def name_exists(name: str) -> bool:
    rows = get_client().table("user").select("id").eq("name", name).limit(1).execute().data
    return bool(rows)


def insert_user(name: str, password_hash: str) -> dict:
    """user 행을 만들고 {"name", "created_at"}을 돌려준다. 아이디가 겹치면 NameTakenError."""
    if name_exists(name):
        raise NameTakenError(name)
    try:
        row = get_client().table("user").insert({"name": name, "password": password_hash}).execute().data[0]
    except APIError as e:
        if e.code == UNIQUE_VIOLATION:   # 동시에 같은 아이디로 가입한 경우 (name에 UNIQUE 제약이 있을 때)
            raise NameTakenError(name) from e
        raise
    return {"name": row["name"], "created_at": row["created_at"]}


def fetch_login(name: str) -> dict | None:
    """로그인 확인용 {"id", "password"}. 없으면 None."""
    rows = get_client().table("user").select("id, password").eq("name", name).limit(1).execute().data
    return rows[0] if rows else None


def fetch_user(user_id: int) -> dict | None:
    """user 테이블의 {"name", "created_at"} (id·password는 내보내지 않는다). 없으면 None."""
    rows = get_client().table("user").select("name, created_at").eq("id", user_id).limit(1).execute().data
    return rows[0] if rows else None


class IdentityColumnError(Exception):
    """info.id가 identity(자동 증가) 열이라 user.id를 넣을 수 없음. DB 설정 문제."""


CANNOT_INSERT_IDENTITY = "428C9"   # GENERATED ALWAYS AS IDENTITY 열에 값을 넣으려 함


def upsert_user_info(user_id: int, age: int, height: int, weight: int) -> dict:
    """info 행을 만들거나, 이미 있으면 덮어쓴다 (info.id = user.id)."""
    try:
        row = (
            get_client().table("info")
            .upsert({"id": user_id, "age": age, "height": height, "weight": weight}, on_conflict="id")
            .execute()
            .data[0]
        )
    except APIError as e:
        if e.code == CANNOT_INSERT_IDENTITY:
            raise IdentityColumnError(e.message) from e
        raise
    return {"age": row["age"], "height": row["height"], "weight": row["weight"]}


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
