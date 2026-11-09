# 캠퍼스 일정(수업 등)과 운동 시간.
#
# - busy_slots: 계획 기간의 바쁜 시간(수업 등). 지금은 연동된 캘린더가 없어 모든 날을 공강으로 본다.
#   나중에 Supabase DB에서 사용자의 시간표를 불러오도록 이 함수만 바꾸면 된다.
# - schedule_text: 프롬프트의 [캠퍼스 일정] 부분
# - add_session_times: AI가 정한 시작 시각과 운동별 예상 시간으로 종료 시각·총 시간을 서버가 계산한다

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.config import WORKOUT_HOURS


@dataclass
class BusySlot:
    date: date
    start: str   # "HH:mm"
    end: str     # "HH:mm"
    title: str   # 예: "자료구조 수업"


def busy_slots(start: date, end: date) -> list[BusySlot]:
    # start~end 기간의 바쁜 시간. TODO: Supabase의 사용자 시간표 연동. 지금은 캘린더가 없으므로 모두 공강.
    return []


def schedule_text(slots: list[BusySlot]) -> str:
    # [캠퍼스 일정] 부분.
    open_, close = WORKOUT_HOURS
    lines = [f"- 운동 가능 시간: 매일 {open_}~{close}"]
    if not slots:
        lines.append("- 수업 등 일정: 없음 (기간 내 모든 날 공강)")
    else:
        lines.append("- 수업 등 일정 (이 시간에는 운동할 수 없음):")
        lines += [f"  {s.date.isoformat()} {s.start}~{s.end} {s.title}" for s in slots]
    return "\n".join(lines)


def parse_hhmm(value: str) -> datetime:
    # "HH:mm" -> datetime (날짜는 의미 없음). 형식이 틀리면 ValueError.
    return datetime.strptime(value, "%H:%M")


def add_session_times(plan: dict) -> dict:
    # 각 날에 total_minutes(운동별 예상 시간 합)와 end_time(start_time + total_minutes)을 붙인다.
    for session in plan["sessions"]:
        total = sum(e["estimated_minutes"] for e in session["exercises"])
        session["total_minutes"] = total
        session["end_time"] = (
            (parse_hhmm(session["start_time"]) + timedelta(minutes=total)).strftime("%H:%M")
            if session["start_time"] else None
        )
    return plan
