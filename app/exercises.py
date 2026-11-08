"""계획에 쓸 수 있는 운동 목록 (exercises.csv).

AI는 이 목록에 있는 운동 이름만 고를 수 있다. 부위·근육 정보는 AI가 아니라 서버가 CSV에서 채운다.
"""

import csv
from pathlib import Path

from app.config import EXERCISES_CSV


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

# 비만(BMI 25 이상)인 사용자에게서 뺄 고강도 운동.
# 대한비만학회 R4-1: 비만 성인이 의사 허가 없이 해도 되는 범위는 저~중강도 운동이다.
# 조깅은 달리기 가이드라인의 "천천히 달리기(약 8km/h)"라 중강도로 본다.
HIGH_INTENSITY_EXERCISES = frozenset({
    "인터벌 사이클", "인터벌 일립티컬", "인터벌 스텝밀", "인터벌 로잉", "고강도 사이클", "줄넘기", "달리기",
})


def allowed_exercise_names(limit_intensity: bool) -> list[str]:
    """사용자가 고를 수 있는 운동 이름 (CSV 순서). limit_intensity면 고강도 운동을 뺀다."""
    return [n for n in EXERCISE_CATALOG if not (limit_intensity and n in HIGH_INTENSITY_EXERCISES)]


def exercise_list_text(names: list[str]) -> str:
    """AI에게 넘길 운동 목록 텍스트. 한 줄 예: - 스쿼트 (부위: 하체 / 근육: 대퇴사두근(허벅지 앞), 둔근(엉덩이))"""
    return "\n".join(
        f"- {name} (부위: {', '.join(EXERCISE_CATALOG[name]['body_parts'])}"
        f" / 근육: {', '.join(EXERCISE_CATALOG[name]['muscles'])})"
        for name in names
    )


def add_exercise_info(plan: dict) -> dict:
    """계획(dict)의 각 운동에 exercises.csv의 부위·근육 정보를 붙인다."""
    for session in plan["sessions"]:
        for exercise in session["exercises"]:
            exercise.update(EXERCISE_CATALOG[exercise["name"]])
    return plan
