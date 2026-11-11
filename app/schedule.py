# 캠퍼스 일정(수업 등)과 운동 시간.
#
# - block_events: 매주 반복 일정(weekly_block)을 계획 기간의 날짜별 일정으로 펼친다
# - daily_busy_events: 매일 운동하기 어려운 시간대(info.busy_time_start~end)를 날짜별 일정으로 펼친다 (다음 날로 넘어가는 경우 포함)
# - schedule_text: 프롬프트의 [캠퍼스 일정] 부분 (반복 일정 + 그 주의 일정 = 운동할 수 없는 시간)
# - fit_sessions: AI가 정한 운동 시간이 일정과 겹치거나 운동 가능 시간을 벗어나면, 그날 가장 가까운 빈 시간으로 옮긴다.
#   그날 빈 시간이 없으면 휴식으로 바꾼다. 서버가 마지막으로 확인하므로 저장되는 운동은 일정과 겹치지 않는다.
# - add_session_times: 종료 시각·총 시간 계산
# - session_rows: 운동하는 날을 schedule 테이블에 저장할 행으로 바꾼다

from datetime import date, datetime, timedelta

from app.config import TIMEZONE, WORKOUT_HOURS
from app.db import ScheduleEvent
from app.week import WEEKDAY_NAMES, days_between

WORKOUT_NAME_PREFIX = "운동: "   # schedule.name 앞에 붙여 운동 일정임을 구분한다 (예: "운동: 가슴·삼두")


def parse_hhmm(value: str) -> datetime:
    # "HH:mm" -> datetime (날짜는 의미 없음). 형식이 틀리면 ValueError.
    return datetime.strptime(value, "%H:%M")


def _at(day: date, hhmm: str) -> datetime:
    # 그날의 "HH:mm" -> 한국 시간 datetime
    t = parse_hhmm(hhmm)
    return datetime(day.year, day.month, day.day, t.hour, t.minute, tzinfo=TIMEZONE)


def _fmt(event: ScheduleEvent) -> str:
    s, e = event.start, event.end
    end = e.strftime("%H:%M") if e.date() == s.date() else e.strftime("%Y-%m-%d %H:%M")
    return f"  {s.strftime('%Y-%m-%d %H:%M')}~{end} {event.name or '(이름 없음)'}"


def block_events(blocks: list[dict], start: date, end: date) -> list[ScheduleEvent]:
    # weekly_block(weekday 1=월 ... 7=일) -> start~end의 날짜별 일정
    return [
        ScheduleEvent(start=_at(d, b["start_time"]), end=_at(d, b["end_time"]), name=b["name"])
        for d in days_between(start, end) for b in blocks if b["weekday"] == d.weekday() + 1
    ]


DAILY_BUSY_NAME = "운동하기 어려운 시간"


def daily_busy_events(busy_start: str | None, busy_end: str | None, start: date, end: date) -> list[ScheduleEvent]:
    # 매일 busy_start~busy_end ("HH:mm")를 start~end의 날짜별 일정으로. busy_end <= busy_start면 다음 날 시각.
    # 다음 날로 넘어가는 시간대는 전날 밤에 시작해 start 날 새벽까지 이어지므로 전날부터 펼친다.
    if not busy_start or not busy_end:
        return []
    overnight = busy_end <= busy_start   # "HH:mm" 문자열은 사전순 = 시간순
    first = start - timedelta(days=1) if overnight else start
    return [
        ScheduleEvent(start=_at(d, busy_start), end=_at(d + timedelta(days=overnight), busy_end), name=DAILY_BUSY_NAME)
        for d in days_between(first, end)
    ]


def daily_busy_text(busy_start: str | None, busy_end: str | None) -> str | None:
    if not busy_start or not busy_end:
        return None
    return f"  매일 {busy_start}~{'다음 날 ' if busy_end <= busy_start else ''}{busy_end} {DAILY_BUSY_NAME}"


def schedule_text(events: list[ScheduleEvent], blocks: list[dict], daily: str | None = None) -> str:
    # [캠퍼스 일정] 부분. 반복 일정은 요일별로, 매일 시간대는 한 줄로 써서 토큰을 아낀다.
    open_, close = WORKOUT_HOURS
    lines = [f"- 운동 가능 시간: 매일 {open_}~{close}"]
    if not events and not blocks and not daily:
        lines.append("- 수업 등 일정: 없음 (기간 내 모든 날 공강)")
        return "\n".join(lines)
    lines.append("- 아래 시간에는 운동할 수 없음")
    if daily:
        lines.append(daily)
    if blocks:
        lines.append("  매주 반복:")
        lines += [f"  매주 {WEEKDAY_NAMES[b['weekday'] - 1]} {b['start_time']}~{b['end_time']} {b['name'] or '(이름 없음)'}"
                  for b in blocks]
    if events:
        lines.append("  이번 기간 일정:")
        lines += [_fmt(e) for e in events]
    return "\n".join(lines)


def _free_gaps(day: date, events: list[ScheduleEvent]) -> list[tuple[datetime, datetime]]:
    # 그날 운동 가능 시간에서 일정을 뺀 빈 구간들
    open_, close = (_at(day, h) for h in WORKOUT_HOURS)
    gaps, cursor = [], open_
    for e in sorted(events, key=lambda e: e.start):
        if e.end <= cursor or e.start >= close:
            continue
        if e.start > cursor:
            gaps.append((cursor, e.start))
        cursor = max(cursor, e.end)
    if cursor < close:
        gaps.append((cursor, close))
    return gaps


def _best_start(day: date, wanted: datetime, minutes: int, events: list[ScheduleEvent]) -> datetime | None:
    # wanted에 가장 가까우면서 minutes만큼 들어가는 시작 시각. 없으면 None.
    length = timedelta(minutes=minutes)
    best = None
    for gap_start, gap_end in _free_gaps(day, events):
        if gap_end - gap_start < length:
            continue
        candidate = min(max(wanted, gap_start), gap_end - length)   # 빈 구간 안으로 당기거나 미룬다
        if best is None or abs(candidate - wanted) < abs(best - wanted):
            best = candidate
    return best


def fit_sessions(plan: dict, events: list[ScheduleEvent]) -> list[dict]:
    # plan의 운동 시간을 일정과 겹치지 않게 고친다(plan을 직접 바꾼다). 바꾼 내역을 돌려준다.
    adjusted = []
    for session in plan["sessions"]:
        if not session["exercises"]:            # 휴식하는 날은 시각이 없다
            session["start_time"] = None
            continue
        day = date.fromisoformat(session["date"])
        minutes = sum(e["estimated_minutes"] for e in session["exercises"])
        original = session["start_time"]
        wanted = _at(day, original or WORKOUT_HOURS[0])
        start = _best_start(day, wanted, minutes, events)
        if start is None:
            adjusted.append({"date": session["date"], "from": original, "to": None,
                             "reason": f"{minutes}분 운동할 빈 시간이 없어 휴식으로 바꿈"})
            session.update(focus="휴식", exercises=[], start_time=None)
        elif start != wanted or original is None:
            new = start.strftime("%H:%M")
            adjusted.append({"date": session["date"], "from": original, "to": new,
                             "reason": "일정과 겹치거나 운동 가능 시간을 벗어나 옮김"})
            session["start_time"] = new
    return adjusted


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


def workout_sessions(plan: dict) -> list[dict]:
    # 저장할 운동하는 날 (운동이 있고 시각이 정해진 날). add_session_times 뒤에 쓴다.
    return [s for s in plan["sessions"] if s["exercises"] and s["start_time"]]


def session_rows(plan: dict) -> list[tuple[str, datetime, datetime]]:
    # 운동하는 날 -> (일정 이름, 시작, 끝). workout_sessions와 같은 순서.
    rows = []
    for s in workout_sessions(plan):
        start = _at(date.fromisoformat(s["date"]), s["start_time"])
        rows.append((WORKOUT_NAME_PREFIX + s["focus"], start, start + timedelta(minutes=s["total_minutes"])))
    return rows


def exercise_rows(session: dict, schedule_id: int) -> list[dict]:
    # 그날의 운동 -> workout_exercise 테이블 행
    return [
        {"schedule_id": schedule_id, "position": i, "name": e["name"], "sets": e["sets"], "reps": e["reps"],
         "duration_seconds": e["duration_seconds"], "estimated_minutes": e["estimated_minutes"]}
        for i, e in enumerate(session["exercises"], 1)
    ]


def overlaps(start: datetime, end: datetime, events: list[ScheduleEvent]) -> list[ScheduleEvent]:
    # [start, end)와 겹치는 일정 (끝과 시작이 맞닿는 것은 겹침 아님)
    return [e for e in events if e.start < end and e.end > start]
