# 사용자 정보로 계산하는 값: 대상(성인/노인 등), BMI 분류.

from app.schemas import PlanRequest


def target_group_of(age: int) -> str:
    # 신체활동 지침서의 대상 구분 (아동·청소년 6~18세, 성인 19~64세, 노인 65세 이상).
    if age <= 18:
        return "아동·청소년"
    if age <= 64:
        return "성인"
    return "노인"


def bmi_category_of(bmi: float, age: int) -> str | None:
    # 대한비만학회 성인 BMI 기준. 아동·청소년은 성인 기준을 쓸 수 없어 None.
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


def build_profile(req: PlanRequest, info: dict) -> dict:
    # 응답의 "profile". info: DB info 테이블의 값 (db.INFO_COLUMNS).
    # 대상과 BMI는 AI가 해석하지 않도록 서버가 계산한다.
    age, height, weight = info["age"], info["height"], info["weight"]
    bmi = round(weight / (height / 100) ** 2, 1)
    return {
        "age": age,
        "sex": "남성" if info["sex"] else "여성",
        "target_group": target_group_of(age),
        "height_cm": height,
        "weight_kg": weight,
        "bmi": bmi,
        "bmi_category": bmi_category_of(bmi, age),
        "goal": req.goal or info.get("goal"),   # 요청에 없으면 info의 목표
        "experience": info.get("experience"),   # 없으면 None
        "uses_gym": info.get("uses_gym"),       # 없으면 None (알 수 없음)
        "inbody": {   # 사용자가 직접 입력한 인바디 최신값. 입력하지 않았으면 모두 None
            "measured_at": info.get("inbody_measured_at"),
            "body_fat_percent": info.get("body_fat_percent"),
            "skeletal_muscle_kg": info.get("skeletal_muscle_kg"),
        },
    }


def limits_intensity(profile: dict) -> bool:
    # 비만(성인 BMI 25 이상)이면 경력과 관계없이 고강도 운동을 빼고 저~중강도만 쓴다.
    return profile["bmi_category"] is not None and profile["bmi"] >= 25
