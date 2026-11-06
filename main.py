import copy
import csv
import hashlib
import json
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

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

client = OpenAI(
    api_key=os.getenv('NOVA_AI_KEY'),
    base_url="https://factchat-cloud.mindlogic.ai/v1/gateway",
)


@app.get("/")
def read_root():

    # response = client.chat.completions.create(
    #     model="claude-sonnet-5",
    #     messages=[{"role": "user", "content": "Hello!"}],
    # )
    # print(response.choices[0].message.content)

    return {"Hello": "World"}


# ---------- AI 호출 (캐시 + 토큰 사용량 기록) ----------
# 같은 요청(모델, 메시지, 응답 형식이 모두 같음)은 저장해 둔 응답을 돌려줘 토큰을 쓰지 않는다.
# LLM_CACHE=0 이면 캐시를 쓰지 않는다.
LLM_CACHE_DIR = Path(__file__).parent / ".llm_cache"
LLM_CACHE_ENABLED = os.getenv("LLM_CACHE", "1") != "0"


def chat_completion(**kwargs) -> str:
    """client.chat.completions.create 결과의 본문 문자열. 캐시가 있으면 API를 부르지 않는다."""
    key = hashlib.sha256(json.dumps(kwargs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache_file = LLM_CACHE_DIR / f"{key}.json"
    if LLM_CACHE_ENABLED and cache_file.exists():
        logger.info("AI 응답 캐시 사용 (토큰 0)")
        return json.loads(cache_file.read_text(encoding="utf-8"))["content"]

    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content
    usage = response.usage
    if usage:
        logger.info("AI 토큰 사용량: 입력 %s / 출력 %s / 합계 %s",
                    usage.prompt_tokens, usage.completion_tokens, usage.total_tokens)
    if LLM_CACHE_ENABLED:
        LLM_CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps({"content": content}, ensure_ascii=False), encoding="utf-8")
    return content


retriever = BM25Retriever()

SYSTEM_PROMPT = (
    "당신은 헬스 운동 관련 전문 상담가로써 사용자의 상태와 목표에 따라 주어진 기간의 헬스 운동 계획을 정해야 한다. "
    "아래에 주어진 [참고 문서]의 내용만을 근거로 사용자의 질문에 한국어로 답변하라. "
    "참고 문서에 없는 내용은 추측하지 마라. 답할 근거가 없으면 found를 false로 하라. "
    "대상(성인, 노인, 아동·청소년 등)이 다른 내용을 섞지 말고 질문 대상에 맞는 내용만 써라. "
    "마크다운 문법을 쓰지 말고 평문으로 작성하라. "
    "일정(Datetime)과 관련한 포맷은 YYYY-MM-DD 혹은 YYYY-MM-DD HH:mm로 한다. "
    "운동은 반드시 [운동 목록]에 있는 운동 이름만 그대로 사용하라. "
    "근력 운동은 [운동 목록]의 부위를 기준으로, 어떤 부위를 운동한 다음 날에는 같은 부위의 운동을 넣지 마라. " # 신체활동 지침서 曰 "근력 운동을 한 신체 부위는 하루 이상 휴식을 취한 후 다시 하는 것이 좋습니다"
    "한 운동이 여러 부위에 속하면(예: 페이스 풀은 등과 어깨) 모든 부위에 이 규칙을 적용하라. "
    "휴식하는 날은 운동이 없어야 한다. "
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


def exercise_list_text(names: list[str]) -> str:
    """AI에게 넘길 운동 목록 텍스트. 한 줄 예: - 스쿼트 (부위: 하체 / 근육: 대퇴사두근(허벅지 앞), 둔근(엉덩이))"""
    return "\n".join(
        f"- {name} (부위: {', '.join(EXERCISE_CATALOG[name]['body_parts'])}"
        f" / 근육: {', '.join(EXERCISE_CATALOG[name]['muscles'])})"
        for name in names
    )


# 비만(BMI 25 이상)인 사용자에게서 뺄 고강도 운동.
# 대한비만학회 R4-1: 비만 성인이 의사 허가 없이 해도 되는 범위는 저~중강도 운동이다.
# 조깅은 달리기 가이드라인의 "천천히 달리기(약 8km/h)"라 중강도로 본다.
HIGH_INTENSITY_EXERCISES = frozenset({
    "인터벌 사이클", "인터벌 일립티컬", "인터벌 스텝밀", "인터벌 로잉", "고강도 사이클", "줄넘기", "달리기",
})
# 스키마 enum으로 쓰여서 AI가 목록 밖의 운동 이름을 낼 수 없다
ExerciseName = Literal[tuple(EXERCISE_CATALOG)]


# ---------- 구조화된 출력 스키마 ----------
# extra="forbid" -> JSON Schema의 additionalProperties: false (strict 모드 필수 조건)

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


# ---------- 검색어 생성 (한국어 + 영어, AI 호출 없이 고정 사전으로) ----------
# 참고 문서에 영문 자료가 섞여 있어 한국어 검색어만으로는 영문 조각이 검색되지 않는다.

TARGET_EN = {
    "유아": "preschool children", "아동·청소년": "children adolescents", "성인": "adults", "노인": "older adults",
    "임산부": "pregnant postpartum women", "만성질환자": "chronic conditions", "장애인": "disability",
}

# (목표에 들어 있으면 해당하는 단어들, 한국어 검색어, 영어 검색어)
GOAL_QUERIES = [
    (("다이어트", "감량", "살", "체지방", "비만"),
     "체중 감량 운동 유산소 근력 병행", "weight loss exercise aerobic resistance"),
    (("근력", "근육", "벌크", "근비대", "증량", "몸만들기"),
     "근력 운동 근비대 세트 반복", "resistance training muscle strength hypertrophy sets repetitions"),
    (("체력", "지구력", "달리기", "마라톤", "러닝", "심폐"),
     "유산소 심폐 지구력 달리기", "aerobic endurance cardiorespiratory fitness running"),
    (("건강", "유지", "자세", "스트레스"),
     "건강 신체활동 권장", "physical activity health recommendations"),
]
GOAL_DEFAULT = ("운동 권장량", "physical activity recommendations")


def build_search_queries(target_group: str, goal: str, experience: str, obese: bool,
                         extra_targets: set[str]) -> tuple[list[str], list[str]]:
    """사용자 정보로 (한국어 검색어, 영어 검색어)를 만든다."""
    ko = [f"{target_group} 신체활동 권장량"]
    en = [f"{TARGET_EN[target_group]} physical activity recommendations"]

    matched = [(k, e) for words, k, e in GOAL_QUERIES if any(w in goal for w in words)] or [GOAL_DEFAULT]
    for k, e in matched:
        ko.append(k)
        en.append(e)

    if experience in ("처음", "6개월 미만"):
        ko.append("운동 초보 시작 점진적")
        en.append("beginners start exercise progression")
    else:
        ko.append("근력 운동 진행 증량")
        en.append("resistance training progression trained")

    if obese:
        ko.append("비만 운동치료 주의사항")
        en.append("obesity exercise therapy adults")

    for t in sorted(extra_targets):
        ko.append(f"{t} 운동")
        en.append(f"{TARGET_EN[t]} exercise")
    return ko, en


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

    # 비만(성인 BMI 25 이상)이면 경력과 관계없이 고강도 운동을 빼고 저~중강도만 쓴다
    limit_intensity = profile["bmi_category"] is not None and bmi >= 25
    allowed_names = [n for n in EXERCISE_CATALOG if not (limit_intensity and n in HIGH_INTENSITY_EXERCISES)]

    user_info = "\n".join([
        f"- 나이: {req.age}세 (대상: {profile['target_group']})",
        f"- 키/몸무게: {req.height_cm}cm / {req.weight_kg}kg",
        f"- BMI: {bmi}" + (f" ({profile['bmi_category']})" if profile["bmi_category"] else ""),
        f"- 헬스 경력: {req.experience}",
        f"- 목표: {req.goal}",
        f"- 건강 상태: {req.health_notes or '특이사항 없음'}",
    ] + (["- 운동 강도 제한: 비만(BMI 25 이상)이므로 저~중강도 운동만 사용"] if limit_intensity else []))

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
    # 한국어(기본 검색어 + 사전 검색어)와 영어(사전 검색어)로 따로 검색해 순위를 합친 뒤 번갈아 채운다.
    # 한 번에 합치면 서로 겹치는 한국어 결과가 점수를 독차지해 영문 자료가 밀려난다.
    ko_queries, en_queries = build_search_queries(
        profile["target_group"], req.goal, req.experience, limit_intensity,
        targets - {profile["target_group"]},
    )
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
            found=False, start_date=start.isoformat(), end_date=end.isoformat(), sessions=[],
        )
        return {"profile": profile, "plan": plan.model_dump(), "sources": []}

    context = "\n\n".join(
        f"[문서 {s['id']}] (출처: {s['source']} / 대상: {', '.join(s['targets'])})\n{s['text']}" for s in sources
    )
    user_message = (
        f"[참고 문서]\n{context}\n\n"
        f"[운동 목록]\n{exercise_list_text(allowed_names)}\n\n"
        f"[계획 기간]\n{start.isoformat()} ~ {end.isoformat()} ({(end - start).days + 1}일)\n\n"
        f"[사용자 정보]\n{user_info}"
    )

    content = chat_completion(
        model="claude-sonnet-5",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "workout_plan", "strict": True, "schema": plan_schema_for(allowed_names)},
        },
    )

    try:
        plan = WorkoutPlan.model_validate_json(content)
    except ValidationError as e:
        raise HTTPException(status_code=502, detail=f"AI 응답 형식 오류: {e}")

    # 스키마로 막았지만, 허용되지 않은 운동이 들어왔으면 그대로 내보내지 않는다
    disallowed = {e.name for s in plan.sessions for e in s.exercises} - set(allowed_names)
    if disallowed:
        raise HTTPException(status_code=502, detail=f"AI 응답에 허용되지 않은 운동 포함: {sorted(disallowed)}")

    return {"profile": profile, "plan": with_exercise_info(plan), "sources": sources}


def with_exercise_info(plan: WorkoutPlan) -> dict:
    """응답의 각 운동에 exercises.csv의 부위·근육 정보를 붙인다."""
    data = plan.model_dump()
    for session in data["sessions"]:
        for exercise in session["exercises"]:
            exercise.update(EXERCISE_CATALOG[exercise["name"]])
    return data
