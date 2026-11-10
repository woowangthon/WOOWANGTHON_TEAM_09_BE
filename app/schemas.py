# 요청과 AI 출력의 형식 (Pydantic 모델).
#
# - PlanRequest: POST /plan 의 요청 본문 (나이·키·몸무게·경력은 DB의 info에서 읽는다)
# - UserCreate(+InfoCreate), LoginRequest, InfoUpdate: POST /user, POST /login, POST /info 의 요청 본문
# - ScheduleIn: POST /schedule, PUT /schedule/{id} 의 요청 본문
# - WorkoutExerciseUpdate: PUT /workout-exercise/{id} 의 요청 본문
# - WeeklyBlockIn: POST /weekly-block, PUT /weekly-block/{id} 의 요청 본문
# - WorkoutPlan: AI가 돌려주는 계획. 이 모델에서 만든 JSON Schema를 AI에게 구조화된 출력 형식으로 준다.

import copy
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import TIMEZONE
from app.week import today
from app.exercises import EXERCISE_CATALOG



# ---------- 사용자 입력 ----------

class PlanRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=200, description="운동 목표 (예: 체중 감량, 근력 향상)")
    week: date | None = Field(
        None, description="이 날짜가 속한 주(월~일)의 계획을 만든다 (YYYY-MM-DD). 없으면 이번 주. 이미 지난 요일은 빼고 짠다",
    )



# ---------- 회원가입 / 로그인 ----------

class UserCreate(BaseModel):
    name: str = Field(
        pattern=r"^[a-z][a-z0-9]{3,19}$",
        description="로그인 아이디. 영문 소문자로 시작, 영문 소문자·숫자 4~20자, 중복 불가 (예: cbnu2026)",
    )
    password: str = Field(min_length=8, max_length=128, description="비밀번호 (8자 이상)")
    info: "InfoCreate | None" = Field(None, description="건강 정보. 주면 가입과 함께 저장 (나중에 POST /info로 넣어도 됨)")


class LoginRequest(BaseModel):
    name: str = Field(max_length=20, description="로그인 아이디")
    password: str = Field(max_length=128)


REQUIRED_INFO = ("age", "height", "weight", "sex")   # info에서 비울 수 없는 열 (DB NOT NULL)


class InfoUpdate(BaseModel):
    # POST /info 본문. 보낸 필드만 바꾼다. 처음 등록할 때는 REQUIRED_INFO가 모두 있어야 한다.
    model_config = ConfigDict(json_schema_extra={"description": (
        "건강 정보. 보낸 필드만 바꾼다 (처음 등록할 때는 age, height, weight, sex 필수). "
        "선택 항목을 지우려면 null을 보낸다. 인바디 값은 사용자가 직접 입력한다."
    )})

    age: int | None = Field(None, ge=6, le=100, description="나이 (만 나이)")
    height: int | None = Field(None, ge=51, le=249, description="키 (cm)")
    weight: int | None = Field(None, ge=11, le=299, description="몸무게 (kg). 인바디를 입력할 때 측정 몸무게로 함께 바꾼다")
    sex: bool | None = Field(None, description="성별 (true = 남성, false = 여성)")
    experience: str | None = Field(
        None, max_length=50, description="헬스 운동 경력, 자유 입력 (예: 처음, 3개월, 1년 반)",
    )
    body_fat_percent: float | None = Field(None, gt=0, lt=80, description="인바디 체지방률 (%)")
    skeletal_muscle_kg: float | None = Field(None, gt=0, lt=100, description="인바디 골격근량 (kg)")
    inbody_measured_at: date | None = Field(None, description="인바디 측정일 (YYYY-MM-DD)")

    @model_validator(mode="after")
    def _check(self):
        for k in REQUIRED_INFO:
            if k in self.model_fields_set and getattr(self, k) is None:
                raise ValueError(f"{k}는 비울 수 없습니다.")
        if self.inbody_measured_at and self.inbody_measured_at > today():
            raise ValueError("inbody_measured_at은 미래일 수 없습니다.")
        return self


class InfoCreate(InfoUpdate):
    # 가입(POST /user)할 때 함께 보내는 건강 정보. 나이·키·몸무게·성별 필수.
    model_config = ConfigDict(json_schema_extra={"description": "가입할 때의 건강 정보 (age, height, weight, sex 필수)."})

    age: int = Field(ge=6, le=100, description="나이 (만 나이)")
    height: int = Field(ge=51, le=249, description="키 (cm)")
    weight: int = Field(ge=11, le=299, description="몸무게 (kg)")
    sex: bool = Field(description="성별 (true = 남성, false = 여성)")


UserCreate.model_rebuild()   # UserCreate.info가 아래에 정의된 InfoCreate를 쓰므로



# ---------- 일정 ----------

class ScheduleIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"description": "시각에 시간대가 없으면(예: 2026-11-10T09:00) 한국 시간으로 본다."})

    name: str | None = Field(None, max_length=100, description="일정 이름 (예: 자료구조 수업)")
    start_term: datetime = Field(description="시작 시각 (예: 2026-11-10T09:00 또는 2026-11-10T09:00+09:00)")
    end_term: datetime = Field(description="마치는 시각. 시작 시각보다 늦어야 함")

    @field_validator("start_term", "end_term")
    @classmethod
    def _assume_kst(cls, v: datetime) -> datetime:
        return v.replace(tzinfo=TIMEZONE) if v.tzinfo is None else v

    @model_validator(mode="after")
    def _check_order(self):
        if self.end_term <= self.start_term:
            raise ValueError("end_term은 start_term보다 늦어야 합니다.")
        return self


class WeeklyBlockIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"description": "매주 반복되는 수업·운동 불가 시간"})

    weekday: int = Field(ge=1, le=7, description="요일 (1=월, 2=화, ..., 7=일)")
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="시작 시각 HH:mm (예: 09:00)")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="마치는 시각 HH:mm. 시작보다 늦어야 함")
    name: str | None = Field(None, max_length=100, description="이름 (예: 자료구조)")

    @model_validator(mode="after")
    def _check_order(self):
        if self.end_time <= self.start_time:   # "HH:mm" 문자열은 사전순 = 시간순
            raise ValueError("end_time은 start_time보다 늦어야 합니다.")
        return self


class WorkoutExerciseUpdate(BaseModel):
    completed: bool = Field(description="이 운동을 했는지")


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
    estimated_minutes: int = Field(description="이 운동의 예상 소요 시간(분). 세트 운동은 세트 사이 휴식 포함")


class DailySession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(description="YYYY-MM-DD")
    focus: str = Field(description="운동 초점 (예: 가슴·삼두, 휴식)")
    start_time: str | None = Field(description="운동 시작 시각 HH:mm (24시간제). [캠퍼스 일정]의 수업과 겹치지 않고 운동 가능 시간 안에 끝나도록. 휴식하는 날은 null")
    exercises: list[Exercise]


class WorkoutPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool = Field(description="참고 문서에 근거가 있는지")
    start_date: str = Field(description="YYYY-MM-DD")
    end_date: str = Field(description="YYYY-MM-DD")
    sessions: list[DailySession] = Field(description="시작일부터 종료일까지 날짜순")
    precaution: str = Field(description="참고 문서에 근거한 주의사항. 한두 문장, 한 줄")


def strip_titles(schema):
    # Pydantic이 자동으로 넣는 "title" 항목을 지운다 (AI에게 보내는 스키마 크기를 줄이려고).
    if isinstance(schema, dict):
        return {k: strip_titles(v) for k, v in schema.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(schema, list):
        return [strip_titles(v) for v in schema]
    return schema


PLAN_SCHEMA = strip_titles(WorkoutPlan.model_json_schema())


def plan_schema_for(names: list[str]) -> dict:
    # 운동 이름 enum을 names로 좁힌 계획 스키마. 사용자에 따라 고를 수 있는 운동을 제한할 때 쓴다.
    schema = copy.deepcopy(PLAN_SCHEMA)
    schema["$defs"]["Exercise"]["properties"]["name"]["enum"] = names
    return schema
