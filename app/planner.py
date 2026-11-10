# 운동 계획 생성의 전체 흐름. 이 파일만 읽으면 요청 하나가 어떻게 처리되는지 알 수 있다.
#
# 1. 기간 확인 (최대 MAX_PLAN_DAYS일)
# 2. 프로필 계산 (DB의 info + 요청의 목표·경력)  -> db.py, profile.py
# 3. 고를 수 있는 운동 정하기                    -> exercises.py
# 4. 참고 문서 조각 고르기 (RAG)                 -> search.py, rag/
# 5. DB의 일정 + 프롬프트 조립                   -> db.py, schedule.py, prompts.py
# 6. AI 호출 (구조화된 출력)                     -> llm.py, schemas.py
# 7. 응답 검증
# 8. 운동 시간을 일정과 겹치지 않게 보정하고 schedule 테이블에 저장  -> schedule.py, db.py

from datetime import date, timedelta

from pydantic import ValidationError

from app.config import AI_MODEL, MAX_PLAN_DAYS
from app.db import fetch_schedules, fetch_user_info, insert_schedules
from app.exercises import add_exercise_info, allowed_exercise_names, exercise_list_text
from app.llm import chat_completion
from app.profile import build_profile, limits_intensity
from app.prompts import SYSTEM_PROMPT, build_user_info, build_user_message
from app.schedule import add_session_times, fit_sessions, overlaps, parse_hhmm, schedule_text, session_rows
from app.schemas import PlanRequest, WorkoutPlan, plan_schema_for
from app.search import select_sources


class PlanError(Exception):
    # 계획을 만들 수 없을 때. api.py가 status_code의 HTTP 오류로 바꾼다.

    def __init__(self, status_code: int, detail):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def create_plan(req: PlanRequest, user_id: int) -> dict:
    # POST /plan 응답: {"profile", "plan", "adjusted", "saved", "sources"}
    # 1. 기간
    start = req.start_date or date.today()
    end = req.end_date or start + timedelta(days=6)
    if end < start:
        raise PlanError(400, "end_date는 start_date보다 빠를 수 없습니다.")
    if (end - start).days + 1 > MAX_PLAN_DAYS:
        raise PlanError(400, f"계획 기간은 최대 {MAX_PLAN_DAYS}일입니다.")

    # 2. 프로필 (나이·키·몸무게는 DB에서)
    info = fetch_user_info(user_id)
    if info is None:
        raise PlanError(400, "건강 정보(나이, 키, 몸무게)가 없습니다. POST /info로 먼저 등록하세요.")
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
        return {"profile": profile, "plan": plan.model_dump(), "adjusted": [], "saved": [], "sources": []}

    # 5. DB의 일정 + 프롬프트
    events = fetch_schedules(user_id, start, end)
    user_info = build_user_info(profile, limit_intensity)
    user_message = build_user_message(
        sources, exercise_list_text(allowed_names), start, end, schedule_text(events), user_info,
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
        if s.start_time is not None:
            try:
                parse_hhmm(s.start_time)
            except ValueError:
                raise PlanError(502, f"AI 응답의 시작 시각 형식 오류 ({s.date}): {s.start_time}")

    # 8. 일정과 겹치지 않게 보정한 뒤 저장
    plan_dict = add_exercise_info(plan.model_dump())
    adjusted = fit_sessions(plan_dict, events)
    add_session_times(plan_dict)
    rows = session_rows(plan_dict)

    # AI가 계획을 만드는 동안 사용자가 일정을 추가했을 수 있으므로 저장 직전에 다시 확인한다
    latest = fetch_schedules(user_id, start, end)
    conflicts = [
        {"date": s.isoformat()[:10], "workout": n, "schedule": e.name}
        for n, s, t in rows for e in overlaps(s, t, latest)
    ]
    if conflicts:
        raise PlanError(409, {"message": "계획을 만드는 동안 추가된 일정과 겹칩니다. 다시 요청하세요.",
                              "conflicts": conflicts})
    saved = insert_schedules(user_id, rows)

    return {"profile": profile, "plan": plan_dict, "adjusted": adjusted, "saved": saved, "sources": sources}
