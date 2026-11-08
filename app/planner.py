"""운동 계획 생성의 전체 흐름. 이 파일만 읽으면 요청 하나가 어떻게 처리되는지 알 수 있다.

1. 기간 확인
2. 프로필 계산 (대상, BMI)            -> profile.py
3. 고를 수 있는 운동 정하기            -> exercises.py
4. 참고 문서 조각 고르기 (RAG)         -> search.py, rag/
5. 프롬프트 조립                       -> prompts.py
6. AI 호출 (구조화된 출력)             -> llm.py, schemas.py
7. 응답 검증 후 운동 부위·근육 정보 붙이기
"""

from datetime import date, timedelta

from pydantic import ValidationError

from app.config import AI_MODEL
from app.exercises import add_exercise_info, allowed_exercise_names, exercise_list_text
from app.llm import chat_completion
from app.profile import build_profile, limits_intensity
from app.prompts import SYSTEM_PROMPT, build_user_info, build_user_message
from app.schemas import PlanRequest, WorkoutPlan, plan_schema_for
from app.search import select_sources


class PlanError(Exception):
    """계획을 만들 수 없을 때. api.py가 status_code의 HTTP 오류로 바꾼다."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def create_plan(req: PlanRequest) -> dict:
    """/output 응답: {"profile": ..., "plan": ..., "sources": [...]}"""
    # 1. 기간
    start = req.start_date or date.today()
    end = req.end_date or start + timedelta(days=6)
    if end < start:
        raise PlanError(400, "end_date는 start_date보다 빠를 수 없습니다.")

    # 2. 프로필
    profile = build_profile(req)
    limit_intensity = limits_intensity(profile)

    # 3. 고를 수 있는 운동
    allowed_names = allowed_exercise_names(limit_intensity)

    # 4. 참고 문서
    sources = select_sources(profile, limit_intensity, req.top_k)
    if not sources:
        plan = WorkoutPlan(
            found=False, start_date=start.isoformat(), end_date=end.isoformat(), sessions=[],
        )
        return {"profile": profile, "plan": plan.model_dump(), "sources": []}

    # 5. 프롬프트
    user_info = build_user_info(profile, limit_intensity)
    user_message = build_user_message(sources, exercise_list_text(allowed_names), start, end, user_info)

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

    return {"profile": profile, "plan": add_exercise_info(plan.model_dump()), "sources": sources}
