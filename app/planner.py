# 운동 계획 생성의 전체 흐름. 이 파일만 읽으면 요청 하나가 어떻게 처리되는지 알 수 있다.
#
# 1. 기간 정하기 (요청한 날짜가 속한 주의 월~일, 지난 요일 제외)  -> week.py
# 2. 프로필 계산 (DB의 info + 요청의 목표·경력)  -> db.py, profile.py
# 3. 고를 수 있는 운동 정하기                    -> exercises.py
# 4. 참고 문서 조각 고르기 (RAG)                 -> search.py, rag/
# 5. DB의 일정(반복 일정 포함) + 지난주 수행률 + 프롬프트 조립  -> db.py, schedule.py, report.py, prompts.py
# 6. AI 호출 (구조화된 출력)                     -> llm.py, schemas.py
# 7. 응답 검증
# 8. 운동 시간을 일정과 겹치지 않게 보정하고 schedule, workout_exercise 테이블에 저장  -> schedule.py, db.py
#    (다른 계획 추천(regenerate)이면 저장에 성공한 뒤 그 주의 오늘 이후 이전 AI 운동 일정을 지운다)

from datetime import date

from pydantic import ValidationError

from app.config import AI_MODEL
from app.db import (
    delete_schedules_by_ids, fetch_ai_workouts, fetch_schedules, fetch_user_info, insert_schedules,
    insert_workout_exercises,
    list_weekly_blocks,
)
from app.exercises import add_exercise_info, allowed_exercise_names, exercise_list_text
from app.llm import chat_completion
from app.profile import build_profile, limits_intensity
from app.prompts import SYSTEM_PROMPT, build_user_info, build_user_message
from app.report import history_text
from app.schedule import (
    add_session_times, block_events, daily_busy_events, daily_busy_text, exercise_rows, fit_sessions, overlaps,
    parse_hhmm, schedule_text, session_rows,
    workout_sessions,
)
from app.schemas import PlanRequest, WorkoutPlan, plan_schema_for
from app.search import select_sources
from app.week import WEEKDAY_NAMES, today, week_bounds


class PlanError(Exception):
    # 계획을 만들 수 없을 때. api.py가 status_code의 HTTP 오류로 바꾼다.

    def __init__(self, status_code: int, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def create_plan(req: PlanRequest, user_id: int) -> dict:
    # POST /plan 응답: {"profile", "plan", "adjusted", "saved", "replaced", "sources"}
    # 1. 기간: 요청 날짜(없으면 오늘)가 속한 주의 월~일. 이미 지난 요일은 뺀다 (수요일에 요청하면 수~일)
    monday, end = week_bounds(req.week or today())
    start = max(monday, today())
    if start > end:
        raise PlanError(400, f"이미 지난 주입니다 ({monday.isoformat()} ~ {end.isoformat()}).")

    # 2. 프로필 (나이·키·몸무게는 DB에서)
    info = fetch_user_info(user_id)
    if info is None:
        raise PlanError(400, "건강 정보(나이, 키, 몸무게)가 없습니다. POST /info로 먼저 등록하세요.")
    if not (req.goal or info.get("goal")):
        raise PlanError(400, "운동 목표가 없습니다. 요청에 goal을 넣거나 POST /info로 goal을 저장하세요.")
    profile = build_profile(req, info)
    limit_intensity = limits_intensity(profile)

    # 3. 고를 수 있는 운동
    allowed_names = allowed_exercise_names(limit_intensity)

    # 4. 참고 문서
    sources = select_sources(profile, limit_intensity)
    if not sources:
        plan = WorkoutPlan(
            found=False, start_date=start.isoformat(), end_date=end.isoformat(), sessions=[], precaution="",
        )
        return {"profile": profile, "plan": plan.model_dump(), "adjusted": [], "saved": [], "replaced": 0, "sources": []}

    # 5. DB의 일정(그 주의 일정 + 매주 반복 일정) + 지난주 수행률 + 프롬프트
    # 다른 계획 추천이면 바꿀 이전 AI 운동 일정(오늘 이후)은 바쁜 시간에서 빼고, 프롬프트에 이전 계획으로 보여준다
    previous = fetch_ai_workouts(user_id, start, end) if req.regenerate else []
    replace_ids = {w["id"] for w in previous}
    events = [e for e in fetch_schedules(user_id, start, end) if e.id not in replace_ids]
    blocks = list_weekly_blocks(user_id)
    busy_times = info.get("busy_times") or []   # 매일 방해 금지 시간대
    daily = daily_busy_events(busy_times, start, end)
    busy = events + block_events(blocks, start, end) + daily
    user_info = build_user_info(profile, limit_intensity)
    user_message = build_user_message(
        sources, exercise_list_text(allowed_names), start, end,
        schedule_text(events, blocks, daily_busy_text(busy_times)),
        history_text(user_id, monday), user_info, previous_text(previous),
    )

    # 6. AI 호출
    content = chat_completion(
        model=AI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "workout_plan", "strict": True, "schema": plan_schema_for(allowed_names)},
        },
    )

    # 7. 검증
    try:
        plan = WorkoutPlan.model_validate_json(content)
    except ValidationError as e:
        raise PlanError(502, f"AI 응답 형식 오류: {e}")

    # 스키마로 막았지만, 허용되지 않은 운동이 들어왔으면 그대로 내보내지 않는다
    disallowed = {e.name for s in plan.sessions for e in s.exercises} - set(allowed_names)
    if disallowed:
        raise PlanError(502, f"AI 응답에 허용되지 않은 운동 포함: {sorted(disallowed)}")

    for s in plan.sessions:
        try:
            date.fromisoformat(s.date)
        except ValueError:
            raise PlanError(502, f"AI 응답의 날짜 형식 오류: {s.date}")
        if s.start_time is not None:
            try:
                parse_hhmm(s.start_time)
            except ValueError:
                raise PlanError(502, f"AI 응답의 시작 시각 형식 오류 ({s.date}): {s.start_time}")

    # 8. 일정과 겹치지 않게 보정한 뒤 저장
    plan_dict = add_exercise_info(plan.model_dump())
    in_range = [s for s in plan_dict["sessions"] if start <= date.fromisoformat(s["date"]) <= end]
    adjusted = [{"date": s["date"], "from": s["start_time"], "to": None, "reason": "계획 기간 밖의 날짜라 뺌"}
                for s in plan_dict["sessions"] if s not in in_range]
    plan_dict.update(sessions=in_range, start_date=start.isoformat(), end_date=end.isoformat())
    adjusted += fit_sessions(plan_dict, busy)
    add_session_times(plan_dict)
    rows = session_rows(plan_dict)

    # AI가 계획을 만드는 동안 사용자가 일정을 추가했을 수 있으므로 저장 직전에 다시 확인한다
    latest = ([e for e in fetch_schedules(user_id, start, end) if e.id not in replace_ids]
              + block_events(list_weekly_blocks(user_id), start, end) + daily)
    conflicts = [
        {"date": s.isoformat()[:10], "workout": n, "schedule": e.name}
        for n, s, t in rows for e in overlaps(s, t, latest)
    ]
    if conflicts:
        raise PlanError(409, {"message": "계획을 만드는 동안 추가된 일정과 겹칩니다. 다시 요청하세요.",
                              "conflicts": conflicts})
    saved = save_workouts(user_id, plan_dict, rows)
    delete_schedules_by_ids(user_id, sorted(replace_ids))   # 새 계획이 저장된 뒤에만 이전 계획을 지운다

    return {"profile": profile, "plan": plan_dict, "adjusted": adjusted, "saved": saved,
            "replaced": len(replace_ids), "sources": sources}


def previous_text(workouts: list[dict]) -> str | None:
    # 다른 계획 추천용 [이전 계획] 부분. 이전 계획이 없으면 None (프롬프트에 넣지 않음).
    if not workouts:
        return None
    lines = [
        f"- {w['start'].strftime('%m-%d')}({WEEKDAY_NAMES[w['start'].weekday()]}) {w['start'].strftime('%H:%M')} "
        f"{(w['name'] or '').removeprefix('운동: ')}: {', '.join(w['exercises'])}"
        for w in workouts
    ]
    return "\n".join(lines + ["- 사용자가 다른 계획을 원한다. 위 계획과 운동 구성·요일·시간을 다르게 짜라."])


def save_workouts(user_id: int, plan_dict: dict, rows: list) -> list[dict]:
    # 운동하는 날을 schedule에, 그날의 운동을 workout_exercise에 저장한다.
    # 운동 저장이 실패하면 방금 만든 일정도 지워, 운동 목록 없는 일정이 남지 않게 한다.
    saved = insert_schedules(user_id, rows, created_by_ai=True)
    by_start = {s["start_term"]: s for s in saved}   # "YYYY-MM-DDTHH:MM:SS" -> 저장된 일정 (하루에 운동 일정은 하나)
    try:
        exercise_data = []
        for session in workout_sessions(plan_dict):
            schedule = by_start[f"{session['date']}T{session['start_time']}:00"]
            exercise_data += exercise_rows(session, schedule["id"])
        stored = insert_workout_exercises(exercise_data)
    except Exception:
        delete_schedules_by_ids(user_id, [s["id"] for s in saved])
        raise

    for schedule in saved:
        schedule["exercises"] = [
            {k: v for k, v in e.items() if k != "schedule_id"}
            for e in sorted(stored, key=lambda e: e["position"]) if e["schedule_id"] == schedule["id"]
        ]
    return saved
