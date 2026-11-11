# Supabase DB 접근.
#
# user.password(해시)는 로그인 확인(fetch_login)에서만 읽고, 다른 곳에서는 조회하지 않는다.
#
# 테이블
# - user:     id(내부 식별자), created_at, name(로그인 아이디), password(Argon2 해시)
# - info:     id(-> user.id), age, height, weight, sex(boolean, true = 남성), experience(text, null 가능),
#             body_fat_percent, skeletal_muscle_kg, inbody_measured_at (인바디 최신값, null 가능),
#             nickname, goal, uses_gym (운동하기 어려운 시간은 weekly_block, schedule에 등록)
# - schedule: author(-> user.id), id, start_term, end_term (timestamptz), name,
#             created_by_ai (기본 false. /plan이 저장한 일정만 true. 사용자 입력으로는 바꿀 수 없다)
# - workout_exercise: id, schedule_id(-> schedule.id, 일정 삭제 시 함께 삭제), position, name,
#             sets, reps, duration_seconds, estimated_minutes, completed (운동별 이수 여부, 기본 false)
# - weekly_block: id, author(-> user.id), weekday(1=월 ... 7=일), start_time, end_time (time), name
#             매주 반복되는 수업·운동 불가 시간
#
# 연결 확인:  python -m app.db <user_id>

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
    id: int | None = None   # schedule.id (반복 일정에서 펼친 것은 None)


@lru_cache(maxsize=1)
def get_client() -> Client:
    # 처음 쓸 때 한 번만 연결한다. 키가 없어도 서버는 뜨고, DB를 쓰는 순간 오류가 난다.
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(".env에 SUPABASE_URL, SUPABASE_KEY가 없습니다.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)


class NameTakenError(Exception):
    # 같은 name(로그인 아이디)의 사용자가 이미 있음.
    pass


UNIQUE_VIOLATION = "23505"   # PostgreSQL 오류 코드


def name_exists(name: str) -> bool:
    rows = get_client().table("user").select("id").eq("name", name).limit(1).execute().data
    return bool(rows)


def insert_user(name: str, password_hash: str) -> dict:
    # user 행을 만들고 {"id", "name", "created_at"}을 돌려준다. 아이디가 겹치면 NameTakenError.
    # id는 내부 식별자이므로 응답에 넣지 않는다 (api.py에서 뺀다).
    if name_exists(name):
        raise NameTakenError(name)
    try:
        row = get_client().table("user").insert({"name": name, "password": password_hash}).execute().data[0]
    except APIError as e:
        if e.code == UNIQUE_VIOLATION:   # 동시에 같은 아이디로 가입한 경우 (name에 UNIQUE 제약이 있을 때)
            raise NameTakenError(name) from e
        raise
    return {"id": row["id"], "name": row["name"], "created_at": row["created_at"]}


def fetch_login(name: str) -> dict | None:
    # 로그인 확인용 {"id", "password"}. 없으면 None.
    rows = get_client().table("user").select("id, password").eq("name", name).limit(1).execute().data
    return rows[0] if rows else None


def fetch_user(user_id: int) -> dict | None:
    # user 테이블의 {"name", "created_at"} (id·password는 내보내지 않는다). 없으면 None.
    rows = get_client().table("user").select("name, created_at").eq("id", user_id).limit(1).execute().data
    return rows[0] if rows else None


INFO_COLUMNS = (
    "age", "height", "weight", "sex", "experience", "body_fat_percent", "skeletal_muscle_kg", "inbody_measured_at",
    "nickname", "goal", "uses_gym",
)


class IdentityColumnError(Exception):
    # info.id가 identity(자동 증가) 열이라 user.id를 넣을 수 없음. DB 설정 문제.
    pass


CANNOT_INSERT_IDENTITY = "428C9"   # GENERATED ALWAYS AS IDENTITY 열에 값을 넣으려 함


def upsert_user_info(user_id: int, info: dict) -> dict:
    # info 행을 만들거나, 이미 있으면 덮어쓴다 (info.id = user.id). info: INFO_COLUMNS의 전체 값.
    # (Postgres upsert는 NOT NULL 열이 모두 있어야 해서, 일부만 바꿀 때도 합친 전체 값을 보낸다)
    try:
        row = (
            get_client().table("info")
            .upsert({"id": user_id, **{k: info.get(k) for k in INFO_COLUMNS}}, on_conflict="id")
            .execute()
            .data[0]
        )
    except APIError as e:
        if e.code == CANNOT_INSERT_IDENTITY:
            raise IdentityColumnError(e.message) from e
        raise
    return {k: row[k] for k in INFO_COLUMNS}


def fetch_user_info(user_id: int) -> dict | None:
    # info 테이블의 INFO_COLUMNS 값. 없으면 None.
    rows = (
        get_client().table("info")
        .select(", ".join(INFO_COLUMNS))
        .eq("id", user_id)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def fetch_schedules(user_id: int, start: date, end: date) -> list[ScheduleEvent]:
    # start~end(포함) 기간과 겹치는 사용자의 일정. 시작 시각 순.
    range_start = datetime.combine(start, time.min, TIMEZONE)
    range_end = datetime.combine(end + timedelta(days=1), time.min, TIMEZONE)
    rows = (
        get_client().table("schedule")
        .select("id, start_term, end_term, name")
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
            id=r["id"],
        )
        for r in rows
    ]


# ---------- schedule CRUD (모두 author = 로그인한 사용자로 제한) ----------

SCHEDULE_COLUMNS = "id, name, start_term, end_term, created_by_ai"


def _local_time(value: str) -> str:
    # DB의 timestamptz -> 한국 시간의 "YYYY-MM-DDTHH:MM:SS" (시간대 표기 없음).
    return datetime.fromisoformat(value).astimezone(TIMEZONE).replace(tzinfo=None).isoformat(timespec="seconds")


def _schedule_out(row: dict) -> dict:
    # DB 행 -> 응답. 시각은 한국 시간으로 바꾸고 시간대 표기(+09:00)는 뺀다.
    return {
        "id": row["id"],
        "name": row["name"],
        "start_term": _local_time(row["start_term"]),
        "end_term": _local_time(row["end_term"]),
        "created_by_ai": row["created_by_ai"],
    }


def list_schedules(user_id: int, start: date | None = None, end: date | None = None) -> list[dict]:
    # 사용자의 일정. start/end(포함)를 주면 그 기간과 겹치는 일정만. 시작 시각 순.
    query = get_client().table("schedule").select(SCHEDULE_COLUMNS).eq("author", user_id)
    if end is not None:
        query = query.lt("start_term", datetime.combine(end + timedelta(days=1), time.min, TIMEZONE).isoformat())
    if start is not None:
        query = query.gt("end_term", datetime.combine(start, time.min, TIMEZONE).isoformat())
    return [_schedule_out(r) for r in query.order("start_term").execute().data]


def get_schedule(user_id: int, schedule_id: int) -> dict | None:
    rows = (
        get_client().table("schedule").select(SCHEDULE_COLUMNS)
        .eq("author", user_id).eq("id", schedule_id).limit(1).execute().data
    )
    return _schedule_out(rows[0]) if rows else None


class ScheduleConflictError(Exception):
    # 다른 일정과 시간이 겹침. conflicts: 겹치는 일정 목록(응답 형식).

    def __init__(self, conflicts: list[dict]):
        super().__init__("일정 시간이 겹침")
        self.conflicts = conflicts


def overlapping_schedules(user_id: int, start_term: datetime, end_term: datetime,
                          exclude_id: int | None = None) -> list[dict]:
    # [start_term, end_term)과 겹치는 본인 일정. 끝과 시작이 맞닿는 것(10:00 끝, 10:00 시작)은 겹침이 아니다.
    query = (
        get_client().table("schedule").select(SCHEDULE_COLUMNS)
        .eq("author", user_id)
        .lt("start_term", end_term.isoformat())    # 새 일정이 끝나기 전에 시작하고
        .gt("end_term", start_term.isoformat())    # 새 일정이 시작한 뒤에 끝나는 일정
    )
    if exclude_id is not None:                     # 수정할 때는 자기 자신 제외
        query = query.neq("id", exclude_id)
    return [_schedule_out(r) for r in query.order("start_term").execute().data]


def insert_schedule(user_id: int, name: str | None, start_term: datetime, end_term: datetime) -> dict:
    # 겹치는 일정이 있으면 ScheduleConflictError.
    if conflicts := overlapping_schedules(user_id, start_term, end_term):
        raise ScheduleConflictError(conflicts)
    row = (
        get_client().table("schedule")
        .insert({"author": user_id, "name": name,
                 "start_term": start_term.isoformat(), "end_term": end_term.isoformat()})
        .execute().data[0]
    )
    return _schedule_out(row)


def update_schedule(user_id: int, schedule_id: int, name: str | None,
                    start_term: datetime, end_term: datetime) -> dict | None:
    # 본인 일정이 아니거나 없으면 None. 다른 일정과 겹치면 ScheduleConflictError.
    if get_schedule(user_id, schedule_id) is None:
        return None
    if conflicts := overlapping_schedules(user_id, start_term, end_term, exclude_id=schedule_id):
        raise ScheduleConflictError(conflicts)
    rows = (
        get_client().table("schedule")
        .update({"name": name, "start_term": start_term.isoformat(), "end_term": end_term.isoformat()})
        .eq("author", user_id).eq("id", schedule_id)
        .execute().data
    )
    return _schedule_out(rows[0]) if rows else None


def insert_schedules(user_id: int, rows: list[tuple[str | None, datetime, datetime]],
                     created_by_ai: bool = False) -> list[dict]:
    # 여러 일정을 한 번의 요청으로 저장한다 (전부 저장되거나 전부 실패). rows: (이름, 시작, 끝).
    # created_by_ai: /plan이 만든 일정이면 True. 사용자 요청(ScheduleIn)에는 이 값이 없어 바꿀 수 없다.
    # 겹침 검사는 하지 않으므로, 부르는 쪽에서 먼저 확인한다 (planner).
    if not rows:
        return []
    data = (
        get_client().table("schedule")
        .insert([{"author": user_id, "name": n, "start_term": s.isoformat(), "end_term": e.isoformat(),
                  "created_by_ai": created_by_ai}
                 for n, s, e in rows])
        .execute().data
    )
    return [_schedule_out(r) for r in data]


def delete_schedule(user_id: int, schedule_id: int) -> bool:
    # 지웠으면 True, 본인 일정이 아니거나 없으면 False.
    rows = get_client().table("schedule").delete().eq("author", user_id).eq("id", schedule_id).execute().data
    return bool(rows)


# ---------- workout_exercise (AI 운동 일정의 운동 목록과 이수 여부) ----------

WORKOUT_EXERCISE_COLUMNS = "id, position, name, sets, reps, duration_seconds, estimated_minutes, completed"


def insert_workout_exercises(rows: list[dict]) -> list[dict]:
    # rows: {"schedule_id", "position", "name", "sets", "reps", "duration_seconds", "estimated_minutes"}.
    # 한 번의 요청으로 저장한다. 저장된 행(schedule_id 포함)을 돌려준다.
    if not rows:
        return []
    return get_client().table("workout_exercise").insert(rows).execute().data


def list_workout_exercises(schedule_id: int) -> list[dict]:
    # 일정 하나의 운동 목록 (순서대로). 본인 일정인지는 부르는 쪽에서 먼저 확인한다.
    return (
        get_client().table("workout_exercise").select(WORKOUT_EXERCISE_COLUMNS)
        .eq("schedule_id", schedule_id).order("position").execute().data
    )


def set_workout_exercise_completed(user_id: int, exercise_id: int, completed: bool) -> dict | None:
    # 운동 하나의 이수 여부를 바꾼다. 본인 일정의 운동이 아니거나 없으면 None.
    owned = (
        get_client().table("workout_exercise")
        .select("id, schedule!inner(author)")          # schedule과 조인해 작성자를 확인
        .eq("id", exercise_id).eq("schedule.author", user_id)
        .limit(1).execute().data
    )
    if not owned:
        return None
    rows = (
        get_client().table("workout_exercise")
        .update({"completed": completed}).eq("id", exercise_id).execute().data
    )
    return {k: rows[0][k] for k in WORKOUT_EXERCISE_COLUMNS.split(", ")} if rows else None


def delete_schedules_by_ids(user_id: int, schedule_ids: list[int]) -> None:
    # 여러 일정을 지운다 (/plan 저장 중 실패했을 때 되돌리기용).
    if schedule_ids:
        get_client().table("schedule").delete().eq("author", user_id).in_("id", schedule_ids).execute()


# ---------- 주간 리포트용: AI 운동 일정과 운동별 이수 여부 ----------

def fetch_ai_workouts(user_id: int, start: date, end: date) -> list[dict]:
    # start~end(포함)에 시작하는 AI 운동 일정. 한 번의 요청으로 운동 이름과 운동별 이수 여부까지 가져온다.
    # [{"id", "name", "start": datetime(한국 시간), "completed": [bool, ...], "exercises": [운동 이름, ...]}]
    rows = (
        get_client().table("schedule")
        .select("id, name, start_term, workout_exercise(name, position, completed)")
        .eq("author", user_id).eq("created_by_ai", True)
        .gte("start_term", datetime.combine(start, time.min, TIMEZONE).isoformat())
        .lt("start_term", datetime.combine(end + timedelta(days=1), time.min, TIMEZONE).isoformat())
        .order("start_term")
        .execute().data
    )
    return [
        {"id": r["id"], "name": r["name"],
         "start": datetime.fromisoformat(r["start_term"]).astimezone(TIMEZONE),
         "completed": [e["completed"] for e in r["workout_exercise"]],
         "exercises": [e["name"] for e in sorted(r["workout_exercise"], key=lambda e: e["position"])]}
        for r in rows
    ]


def delete_ai_workouts(user_id: int, start: date, end: date) -> int:
    # start~end(포함)에 시작하는 AI 운동 일정을 지운다 (운동 목록·이수 기록은 DB에서 함께 삭제). 지운 개수.
    rows = (
        get_client().table("schedule").delete()
        .eq("author", user_id).eq("created_by_ai", True)
        .gte("start_term", datetime.combine(start, time.min, TIMEZONE).isoformat())
        .lt("start_term", datetime.combine(end + timedelta(days=1), time.min, TIMEZONE).isoformat())
        .execute().data
    )
    return len(rows)


# ---------- weekly_block (매주 반복되는 수업·운동 불가 시간) ----------

WEEKLY_BLOCK_COLUMNS = "id, weekday, start_time, end_time, name"


def _block_out(row: dict) -> dict:
    # 응답용: 필요한 열만 (author 같은 내부 id는 빼고), DB의 time("09:00:00") -> "09:00"
    return {"id": row["id"], "weekday": row["weekday"], "start_time": row["start_time"][:5],
            "end_time": row["end_time"][:5], "name": row["name"]}


class BlockConflictError(Exception):
    # 같은 요일의 다른 반복 일정과 시간이 겹침
    def __init__(self, conflicts: list[dict]):
        super().__init__("반복 일정 시간이 겹침")
        self.conflicts = conflicts


def list_weekly_blocks(user_id: int) -> list[dict]:
    rows = (
        get_client().table("weekly_block").select(WEEKLY_BLOCK_COLUMNS)
        .eq("author", user_id).order("weekday").order("start_time").execute().data
    )
    return [_block_out(r) for r in rows]


def get_weekly_block(user_id: int, block_id: int) -> dict | None:
    rows = (
        get_client().table("weekly_block").select(WEEKLY_BLOCK_COLUMNS)
        .eq("author", user_id).eq("id", block_id).limit(1).execute().data
    )
    return _block_out(rows[0]) if rows else None


def _overlapping_blocks(user_id: int, weekday: int, start_time: str, end_time: str,
                        exclude_id: int | None = None) -> list[dict]:
    query = (
        get_client().table("weekly_block").select(WEEKLY_BLOCK_COLUMNS)
        .eq("author", user_id).eq("weekday", weekday)
        .lt("start_time", end_time).gt("end_time", start_time)
    )
    if exclude_id is not None:
        query = query.neq("id", exclude_id)
    return [_block_out(r) for r in query.execute().data]


def insert_weekly_block(user_id: int, weekday: int, start_time: str, end_time: str, name: str | None) -> dict:
    # 같은 요일에 겹치는 반복 일정이 있으면 BlockConflictError.
    if conflicts := _overlapping_blocks(user_id, weekday, start_time, end_time):
        raise BlockConflictError(conflicts)
    row = (
        get_client().table("weekly_block")
        .insert({"author": user_id, "weekday": weekday, "start_time": start_time, "end_time": end_time, "name": name})
        .execute().data[0]
    )
    return _block_out(row)


def update_weekly_block(user_id: int, block_id: int, weekday: int, start_time: str, end_time: str,
                        name: str | None) -> dict | None:
    # 본인 것이 아니거나 없으면 None. 겹치면 BlockConflictError.
    if get_weekly_block(user_id, block_id) is None:
        return None
    if conflicts := _overlapping_blocks(user_id, weekday, start_time, end_time, exclude_id=block_id):
        raise BlockConflictError(conflicts)
    rows = (
        get_client().table("weekly_block")
        .update({"weekday": weekday, "start_time": start_time, "end_time": end_time, "name": name})
        .eq("author", user_id).eq("id", block_id).execute().data
    )
    return _block_out(rows[0]) if rows else None


def delete_weekly_block(user_id: int, block_id: int) -> bool:
    rows = get_client().table("weekly_block").delete().eq("author", user_id).eq("id", block_id).execute().data
    return bool(rows)


if __name__ == "__main__":
    uid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print("info:", fetch_user_info(uid))
    today = date.today()
    for e in fetch_schedules(uid, today, today + timedelta(days=13)):
        print("schedule:", e.start.strftime("%Y-%m-%d %H:%M"), "~", e.end.strftime("%H:%M"), e.name)
