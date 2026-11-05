import csv
import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from rag import BM25Retriever

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI()

client = OpenAI(
    api_key=os.getenv('NOVA_AI_KEY'),
    base_url="https://factchat-cloud.mindlogic.ai/v1/gateway",
)


@app.get("/")
def read_root():

    response = client.chat.completions.create(
        model="claude-sonnet-5",
        messages=[{"role": "user", "content": "Hello!"}],
    )
    print(response.choices[0].message.content)

    return {"Hello": "World"}

retriever = BM25Retriever()

SYSTEM_PROMPT = (
    "당신은 헬스 운동 관련 전문 상담가로써 사용자의 상태와 목표에 따라 주어진 기간의 헬스 운동 계획을 정해야 한다. "
    "아래에 주어진 [참고 문서]의 내용만을 근거로 사용자의 질문에 한국어로 답변하라. "
    "참고 문서에 없는 내용은 추측하지 마라. 답할 근거가 없으면 found를 false로 하라. "
    "대상(성인, 노인, 아동·청소년 등)이 다른 내용을 섞지 말고 질문 대상에 맞는 내용만 써라. "
    "마크다운 문법을 쓰지 말고 평문으로 작성하라. "
    "일정(Datetime)과 관련한 포맷은 YYYY-MM-DD 혹은 YYYY-MM-DD HH:mm로 한다. "
    "운동은 반드시 [운동 목록]에 있는 운동 이름만 그대로 사용하라."
)


# ---------- 운동 목록 (exercises.csv) ----------

EXERCISES_CSV = Path(__file__).parent / "exercises.csv"


def load_exercise_catalog(path: Path = EXERCISES_CSV) -> dict[str, dict[str, list[str]]]:
    """운동 이름 -> {"body_parts": [...], "muscles": [...]}.
    같은 운동이 여러 부위/근육에 걸쳐 나오면(예: 스쿼트, 딥스) 하나로 합친다."""
    catalog: dict[str, dict[str, list[str]]] = {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            body_part, muscle, name = ((row.get(k) or "").strip() for k in ("부위", "근육 중심", "운동 종류"))
            if not name:  # 운동 이름이 비어 있는 미완성 행은 건너뛴다
                continue
            entry = catalog.setdefault(name, {"body_parts": [], "muscles": []})
            for key, value in (("body_parts", body_part), ("muscles", muscle)):
                if value and value not in entry[key]:
                    entry[key].append(value)
    return catalog


EXERCISE_CATALOG = load_exercise_catalog()
# AI에게 넘길 운동 목록 텍스트: "- 스쿼트 (부위: 하체 / 근육: 대퇴사두근(허벅지 앞), 둔근(엉덩이))"
EXERCISE_LIST_TEXT = "\n".join(
    f"- {name} (부위: {', '.join(info['body_parts'])} / 근육: {', '.join(info['muscles'])})"
    for name, info in EXERCISE_CATALOG.items()
)
# 스키마 enum으로 쓰여서 AI가 목록 밖의 운동 이름을 낼 수 없다
ExerciseName = Literal[tuple(EXERCISE_CATALOG)]


# ---------- 구조화된 출력 스키마 ----------
# extra="forbid" -> JSON Schema의 additionalProperties: false (strict 모드 필수 조건)

class Exercise(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ExerciseName = Field(description="운동 이름 ([운동 목록]에 있는 이름만 가능)")
    sets: int | None = Field(description="세트 수. 세트 개념이 없으면 null")
    reps: int | None = Field(description="세트당 반복 횟수. 플랭크처럼 시간 기반이면 null")
    duration_seconds: int | None = Field(
        description="시간(초). 플랭크처럼 세트가 있으면 세트당 시간, 유산소처럼 세트가 없으면 전체 시간. 횟수 기반 운동이면 null"
    )


class DailySession(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(description="운동 날짜 (YYYY-MM-DD)")
    focus: str = Field(description="그날의 운동 초점 (예: 가슴·삼두, 하체, 휴식)")
    exercises: list[Exercise] = Field(description="그날 수행할 운동 목록")


class WorkoutPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    found: bool = Field(description="참고 문서에서 계획의 근거를 찾았는지 여부")
    start_date: str = Field(description="계획 시작일 (YYYY-MM-DD)")
    end_date: str = Field(description="계획 종료일 (YYYY-MM-DD)")
    sessions: list[DailySession] = Field(description="시작일부터 종료일까지 날짜순 일별 일정")
    precautions: list[str] = Field(description="참고 문서에 근거한 주의사항 목록")


PLAN_SCHEMA = WorkoutPlan.model_json_schema()


# ---------- 사용자 입력 ----------

class PlanRequest(BaseModel):
    age: int = Field(ge=6, le=100, description="나이 (만 나이)")
    height_cm: float = Field(gt=50, lt=250, description="키 (cm)")
    weight_kg: float = Field(gt=10, lt=300, description="몸무게 (kg)")
    goal: str = Field(min_length=1, max_length=200, description="운동 목표 (예: 체중 감량, 근력 향상)")
    experience: Literal["처음", "6개월 미만", "6개월~2년", "2년 이상"] = Field(description="헬스 운동 경력")
    health_notes: str | None = Field(None, max_length=300, description="질환, 부상, 임신 등 참고할 건강 상태")
    start_date: date | None = Field(None, description="계획 시작일 (YYYY-MM-DD). 없으면 오늘")
    end_date: date | None = Field(None, description="계획 종료일 (YYYY-MM-DD). 없으면 시작일로부터 7일")
    top_k: int = Field(4, ge=1, le=10, description="참고할 문서 조각 수")


def target_group_of(age: int) -> str:
    """신체활동 지침서의 대상 구분 (아동·청소년 6~18세, 성인 19~64세, 노인 65세 이상)."""
    if age <= 18:
        return "아동·청소년"
    if age <= 64:
        return "성인"
    return "노인"


def bmi_category_of(bmi: float, age: int) -> str | None:
    """대한비만학회 성인 BMI 기준. 아동·청소년은 성인 기준을 쓸 수 없어 None."""
    if age <= 18:
        return None
    if bmi < 18.5:
        return "저체중"
    if bmi < 23:
        return "정상"
    if bmi < 25:
        return "비만 전단계"
    if bmi < 30:
        return "1단계 비만"
    if bmi < 35:
        return "2단계 비만"
    return "3단계 비만"


HEALTH_NOTE_TARGETS = {
    "임산부": ("임신", "임산부", "출산", "산후"),
    "만성질환자": ("고혈압", "당뇨", "이상지질혈증", "고지혈증", "골다공증", "관절염", "천식", "심장", "만성질환"),
    "장애인": ("장애", "휠체어", "마비"),
}


def targets_from_notes(health_notes: str | None) -> set[str]:
    """건강 상태 메모에 들어 있는 키워드로 추가 대상을 찾는다."""
    if not health_notes:
        return set()
    return {t for t, words in HEALTH_NOTE_TARGETS.items() if any(w in health_notes for w in words)}


# ---------- 검색어 생성 (한국어 + 영어) ----------
# 참고 문서에 영문 자료가 섞여 있어 한국어 검색어만으로는 영문 조각이 검색되지 않는다.

class SearchQueries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ko: list[str] = Field(description="한국어 검색어 2~3개")
    en: list[str] = Field(description="같은 의미의 영어 검색어 2~3개")


QUERY_PROMPT = (
    "당신은 운동 지침 문서 검색을 돕는다. 사용자 정보를 보고, 이 사용자의 운동 계획을 세우는 데 필요한 "
    "운동 권장량, 운동 방법, 주의사항을 찾기 위한 검색어를 만들어라. "
    "대상(성인, 노인 등), 목표, 체형(BMI), 운동 경력, 건강 상태를 반영하라. "
    "검색어는 문장이 아니라 핵심 단어 위주의 짧은 구로 쓰고, 한국어 검색어와 같은 의미의 영어 검색어를 함께 만들어라."
)


def generate_search_queries(user_info: str) -> tuple[list[str], list[str]]:
    """AI로 (한국어 검색어, 영어 검색어)를 만든다. 실패하면 빈 목록을 돌려준다(기본 검색어만 사용)."""
    try:
        response = client.chat.completions.create(
            model="claude-sonnet-5",
            messages=[
                {"role": "system", "content": QUERY_PROMPT},
                {"role": "user", "content": f"[사용자 정보]\n{user_info}"},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "search_queries", "strict": True, "schema": SearchQueries.model_json_schema()},
            },
        )
        queries = SearchQueries.model_validate_json(response.choices[0].message.content)
    except Exception as e:  # API 오류나 형식 오류가 나도 계획 생성은 계속한다
        logger.warning("검색어 생성 실패, 기본 검색어만 사용: %s", e)
        return [], []
    return [q.strip() for q in queries.ko if q.strip()], [q.strip() for q in queries.en if q.strip()]


def interleave(*result_lists: list[tuple]) -> list[tuple]:
    """여러 검색 결과를 번갈아 하나씩 꺼내 합친다 (같은 조각은 한 번만)."""
    merged, seen = [], set()
    for group in zip(*(lst + [None] * (max(map(len, result_lists)) - len(lst)) for lst in result_lists)):
        for item in group:
            if item is not None and id(item[0]) not in seen:
                seen.add(id(item[0]))
                merged.append(item)
    return merged


# ---------- API ----------

@app.get("/output")
def output(req: Annotated[PlanRequest, Query()]):
    start = req.start_date or date.today()
    end = req.end_date or start + timedelta(days=6)
    if end < start:
        raise HTTPException(status_code=400, detail="end_date는 start_date보다 빠를 수 없습니다.")

    bmi = round(req.weight_kg / (req.height_cm / 100) ** 2, 1)
    profile = {
        "age": req.age,
        "target_group": target_group_of(req.age),
        "height_cm": req.height_cm,
        "weight_kg": req.weight_kg,
        "bmi": bmi,
        "bmi_category": bmi_category_of(bmi, req.age),
        "goal": req.goal,
        "experience": req.experience,
        "health_notes": req.health_notes,
    }

    user_info = "\n".join([
        f"- 나이: {req.age}세 (대상: {profile['target_group']})",
        f"- 키/몸무게: {req.height_cm}cm / {req.weight_kg}kg",
        f"- BMI: {bmi}" + (f" ({profile['bmi_category']})" if profile["bmi_category"] else ""),
        f"- 헬스 경력: {req.experience}",
        f"- 목표: {req.goal}",
        f"- 건강 상태: {req.health_notes or '특이사항 없음'}",
    ])

    # 검색 대상: 나이 기준 대상 + 건강 상태로 알 수 있는 대상(임산부, 만성질환자, 장애인)
    targets = {profile["target_group"]} | targets_from_notes(req.health_notes)
    is_obese = bool(profile["bmi_category"] and "비만" in profile["bmi_category"])

    # 기본 검색어: 사용자 문장 대신 대상·목표·체형·건강 상태로 구성
    search_query = " ".join(filter(None, [
        profile["target_group"], "운동 권장", req.goal,
        "비만 운동 시작 전 상담" if is_obese else None,
        req.health_notes,
    ]))

    # 대상의 핵심 지침(권장량)은 항상 넣고, 나머지는 대상이 맞는 청크에서 검색해 채운다
    pinned = [chunk for t in sorted(targets) for chunk in retriever.core_chunks(t)]
    # 한국어(기본 검색어 + AI 검색어)와 영어(AI 검색어)로 따로 검색해 순위를 합친 뒤 번갈아 채운다.
    # 한 번에 합치면 서로 겹치는 한국어 결과가 점수를 독차지해 영문 자료가 밀려난다.
    ko_queries, en_queries = generate_search_queries(user_info)
    slots = max(req.top_k - len(pinned), 0)
    ko_found = retriever.search_many([search_query] + ko_queries, top_k=slots, targets=targets, exclude=pinned)
    en_found = retriever.search_many(en_queries, top_k=slots, targets=targets, exclude=pinned) if en_queries else []
    found = interleave(ko_found, en_found)[:slots]
    results = [(chunk, None) for chunk in pinned] + found
    sources = [
        {
            "id": i, "source": chunk.source, "targets": list(chunk.targets), "pinned": score is None,
            "score": None if score is None else round(score, 3), "text": chunk.text,
        }
        for i, (chunk, score) in enumerate(results, 1)
    ]
    if not results:
        plan = WorkoutPlan(
            found=False, start_date=start.isoformat(), end_date=end.isoformat(),
            sessions=[], precautions=[],
        )
        return {"profile": profile, "plan": plan.model_dump(), "sources": []}

    context = "\n\n".join(
        f"[문서 {s['id']}] (출처: {s['source']} / 대상: {', '.join(s['targets'])})\n{s['text']}" for s in sources
    )
    user_message = (
        f"[참고 문서]\n{context}\n\n"
        f"[운동 목록]\n{EXERCISE_LIST_TEXT}\n\n"
        f"[계획 기간]\n{start.isoformat()} ~ {end.isoformat()} ({(end - start).days + 1}일)\n\n"
        f"[사용자 정보]\n{user_info}"
    )

    response = client.chat.completions.create(
        model="claude-sonnet-5",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "workout_plan", "strict": True, "schema": PLAN_SCHEMA},
        },
    )

    try:
        plan = WorkoutPlan.model_validate_json(response.choices[0].message.content)
    except ValidationError as e:
        raise HTTPException(status_code=502, detail=f"AI 응답 형식 오류: {e}")

    return {"profile": profile, "plan": with_exercise_info(plan), "sources": sources}


def with_exercise_info(plan: WorkoutPlan) -> dict:
    """응답의 각 운동에 exercises.csv의 부위·근육 정보를 붙인다."""
    data = plan.model_dump()
    for session in data["sessions"]:
        for exercise in session["exercises"]:
            exercise.update(EXERCISE_CATALOG[exercise["name"]])
    return data
