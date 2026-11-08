"""AI에게 줄 참고 문서 조각 고르기.

1. 사용자 대상의 핵심 지침(권장량) 조각은 항상 넣는다 (pinned).
2. 나머지 자리는 한국어 검색어와 영어 검색어로 따로 검색한 결과를 번갈아 채운다.
   참고 문서에 영문 자료가 섞여 있어 한국어 검색어만으로는 영문 조각이 검색되지 않고,
   한 번에 합치면 서로 겹치는 한국어 결과가 점수를 독차지해 영문 자료가 밀려난다.
검색어는 AI 호출 없이 고정 사전으로 만든다 (토큰 절약).
"""

from app.profile import targets_from_notes
from app.rag import BM25Retriever, Chunk

# 서버 시작 시 참고 문서를 한 번 읽는다. 문서를 바꾸면 서버를 재시작해야 반영된다.
retriever = BM25Retriever()


# ---------- 검색어 생성 (한국어 + 영어) ----------

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


def base_search_query(profile: dict) -> str:
    """기본 한국어 검색어: 사용자 문장 대신 대상·목표·체형·건강 상태로 구성."""
    is_obese = bool(profile["bmi_category"] and "비만" in profile["bmi_category"])
    return " ".join(filter(None, [
        profile["target_group"], "운동 권장", profile["goal"],
        "비만 운동 시작 전 상담" if is_obese else None,
        profile["health_notes"],
    ]))


def interleave(*result_lists: list[tuple]) -> list[tuple]:
    """여러 검색 결과를 번갈아 하나씩 꺼내 합친다 (같은 조각은 한 번만)."""
    merged, seen = [], set()
    for group in zip(*(lst + [None] * (max(map(len, result_lists)) - len(lst)) for lst in result_lists)):
        for item in group:
            if item is not None and id(item[0]) not in seen:
                seen.add(id(item[0]))
                merged.append(item)
    return merged


# ---------- 참고 문서 선택 ----------

def select_sources(profile: dict, limit_intensity: bool, top_k: int) -> list[dict]:
    """참고 문서 조각 목록. 응답의 "sources"이자, AI에게 보낼 [참고 문서]의 재료다."""
    # 검색 대상: 나이 기준 대상 + 건강 상태로 알 수 있는 대상(임산부, 만성질환자, 장애인)
    targets = {profile["target_group"]} | targets_from_notes(profile["health_notes"])

    pinned: list[Chunk] = [chunk for t in sorted(targets) for chunk in retriever.core_chunks(t)]
    ko_queries, en_queries = build_search_queries(
        profile["target_group"], profile["goal"], profile["experience"], limit_intensity,
        targets - {profile["target_group"]},
    )
    slots = max(top_k - len(pinned), 0)
    ko_found = retriever.search_many([base_search_query(profile)] + ko_queries, top_k=slots, targets=targets, exclude=pinned)
    en_found = retriever.search_many(en_queries, top_k=slots, targets=targets, exclude=pinned) if en_queries else []
    found = interleave(ko_found, en_found)[:slots]

    results = [(chunk, None) for chunk in pinned] + found
    return [
        {
            "id": i, "source": chunk.source, "targets": list(chunk.targets), "pinned": score is None,
            "score": None if score is None else round(score, 3), "text": chunk.text,
        }
        for i, (chunk, score) in enumerate(results, 1)
    ]
