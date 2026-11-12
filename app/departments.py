# 충북대학교 학과 -> 단과대학 (departments.csv). 가입할 때 학과만 받고, 단과대학은 서버가 이 표로 채운다.
#
# 출처: 충북대학교 홈페이지 > 대학/대학원 > 단과대학 (2026-11 확인). 학과가 바뀌면 CSV만 고치면 된다.
# 학과를 입력하지 않은 사용자(null)는 대시보드에서 "기타"로 묶는다.

import csv

from app.config import DEPARTMENTS_CSV

OTHER = "기타"   # 학과·단과대학이 없는 사용자


def _load() -> dict[str, str]:
    with DEPARTMENTS_CSV.open(encoding="utf-8-sig", newline="") as f:
        return {row["학과"].strip(): row["단과대학"].strip() for row in csv.DictReader(f) if row["학과"].strip()}


DEPARTMENT_TO_COLLEGE = _load()
COLLEGES = list(dict.fromkeys(DEPARTMENT_TO_COLLEGE.values()))   # CSV 순서 그대로, 중복 없이


def college_of(department: str | None) -> str | None:
    return DEPARTMENT_TO_COLLEGE.get(department) if department else None
