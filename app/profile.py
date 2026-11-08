"""사용자 입력으로 계산하는 값: 대상(성인/노인 등), BMI 분류, 건강 상태로 알 수 있는 추가 대상."""

from app.schemas import PlanRequest


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


def build_profile(req: PlanRequest) -> dict:
    """응답의 "profile". 대상과 BMI는 AI가 해석하지 않도록 서버가 계산한다."""
    bmi = round(req.weight_kg / (req.height_cm / 100) ** 2, 1)
    return {
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


def limits_intensity(profile: dict) -> bool:
    """비만(성인 BMI 25 이상)이면 경력과 관계없이 고강도 운동을 빼고 저~중강도만 쓴다."""
    return profile["bmi_category"] is not None and profile["bmi"] >= 25
