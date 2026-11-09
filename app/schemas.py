"""요청과 AI 출력의 형식 (Pydantic 모델).

- PlanRequest: /output 이 받는 쿼리 파라미터
- WorkoutPlan: AI가 돌려주는 계획. 이 모델에서 만든 JSON Schema를 AI에게 구조화된 출력 형식으로 준다.
"""

import copy
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.exercises import EXERCISE_CATALOG


# ---------- 사용자 입력 ----------

class PlanRequest(BaseModel):
    age: int = Field(ge=6, le=100, description="나이 (만 나이)")
    height_cm: float = Field(gt=50, lt=250, description="키 (cm)")
    weight_kg: float = Field(gt=10, lt=300, description="몸무게 (kg)")
    goal: str = Field(min_length=1, max_length=200, description="운동 목표 (예: 체중 감량, 근력 향상)")
    experience: Literal["처음", "6개월 미만", "6개월~2년", "2년 이상"] = Field(description="헬스 운동 경력")
    health_notes: str | None = Field(None, max_length=300, description="질환, 부상, 임신 등 참고할 건강 상태")
    start_date: date | None = Field(None, description="계획 시작일 (YYYY-MM-DD). 없으면 오늘")
    end_date: date | None = Field(None, description="계획 종료일 (YYYY-MM-DD). 없으면 시작일로부터 7일. 기간은 최대 14일")
    top_k: int = Field(4, ge=1, le=10, description="참고할 문서 조각 수")


# ---------- AI 출력 (구조화된 출력 스키마) ----------
# extra="forbid" -> JSON Schema의 additionalProperties: false (strict 모드 필수 조건)

# 스키마 enum으로 쓰여서 AI가 목록 밖의 운동 이름을 낼 수 없다
ExerciseName = Literal[tuple(EXERCISE_CATALOG)]


class Exercise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ExerciseName = Field(description="운동 이름")
    sets: int | None = Field(description="세트 수, 없으면 null")
    reps: int | None = Field(description="세트당 반복 횟수, 시간 기반이면 null")
    duration_seconds: int | None = Field(description="시간(초): 세트가 있으면 세트당, 없으면 전체. 횟수 기반이면 null")


class DailySession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(description="YYYY-MM-DD")
    focus: str = Field(description="운동 초점 (예: 가슴·삼두, 휴식)")
    exercises: list[Exercise]


class WorkoutPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool = Field(description="참고 문서에 근거가 있는지")
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: str = Field(description="YYYY-MM-DD")
    sessions: list[DailySession] = Field(description="시작일부터 종료일까지 날짜순")
    precaution: str = Field(description="참고 문서에 근거한 주의사항. 한두 문장, 한 줄")


def strip_titles(schema):
    """Pydantic이 자동으로 넣는 "title" 항목을 지운다 (AI에게 보내는 스키마 크기를 줄이려고)."""
    if isinstance(schema, dict):
        return {k: strip_titles(v) for k, v in schema.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(schema, list):
        return [strip_titles(v) for v in schema]
    return schema


PLAN_SCHEMA = strip_titles(WorkoutPlan.model_json_schema())


def plan_schema_for(names: list[str]) -> dict:
    """운동 이름 enum을 names로 좁힌 계획 스키마. 사용자에 따라 고를 수 있는 운동을 제한할 때 쓴다."""
    schema = copy.deepcopy(PLAN_SCHEMA)
    schema["$defs"]["Exercise"]["properties"]["name"]["enum"] = names
    return schema
