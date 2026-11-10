# AI에게 보내는 프롬프트: 시스템 프롬프트와 사용자 메시지 조립.

from datetime import date

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


def build_user_info(profile: dict, limit_intensity: bool) -> str:
    # [사용자 정보] 부분.
    return "\n".join([
        f"- 나이: {profile['age']}세 (대상: {profile['target_group']})",
        f"- 성별: {profile['sex']}",
        f"- 키/몸무게: {profile['height_cm']}cm / {profile['weight_kg']}kg",
        f"- BMI: {profile['bmi']}" + (f" ({profile['bmi_category']})" if profile["bmi_category"] else ""),
        f"- 헬스 경력: {profile['experience'] or '정보 없음'}",
        f"- 목표: {profile['goal']}",
    ] + gym_lines(profile["uses_gym"])
      + inbody_lines(profile["inbody"])
      + (["- 운동 강도 제한: 비만(BMI 25 이상)이므로 저~중강도 운동만 사용"] if limit_intensity else []))


def gym_lines(uses_gym: bool | None) -> list[str]:
    # 헬스장 이용 여부. 모르면 줄을 넣지 않는다.
    if uses_gym is None:
        return []
    return ["- 헬스장 이용: 가능"] if uses_gym else ["- 헬스장 이용: 불가 (기구 없이 할 수 있는 운동 위주로 고를 것)"]


def inbody_lines(inbody: dict) -> list[str]:
    # 인바디 값이 하나라도 있으면 한 줄. 없으면 줄을 넣지 않는다 (토큰 절약).
    parts = []
    if inbody["body_fat_percent"] is not None:
        parts.append(f"체지방률 {inbody['body_fat_percent']}%")
    if inbody["skeletal_muscle_kg"] is not None:
        parts.append(f"골격근량 {inbody['skeletal_muscle_kg']}kg")
    if not parts:
        return []
    when = f" ({inbody['measured_at']} 측정)" if inbody["measured_at"] else ""
    return [f"- 인바디{when}: {', '.join(parts)}"]


def build_context(sources: list[dict]) -> str:
    # [참고 문서] 부분. 조각마다 번호, 출처, 대상을 붙인다.
    return "\n\n".join(
        f"[문서 {s['id']}] (출처: {s['source']} / 대상: {', '.join(s['targets'])})\n{s['text']}" for s in sources
    )


def build_user_message(sources: list[dict], exercise_list: str, start: date, end: date, schedule: str,
                       history: str, user_info: str, previous: str | None = None) -> str:
    # AI에게 보내는 사용자 메시지 전체. previous: 다른 계획 추천일 때 이전 계획 (없으면 그 부분을 넣지 않는다)
    return (
        (f"[이전 계획]\n{previous}\n\n" if previous else "") +
        f"[참고 문서]\n{build_context(sources)}\n\n"
        f"[운동 목록]\n{exercise_list}\n\n"
        f"[계획 기간]\n{start.isoformat()} ~ {end.isoformat()} ({(end - start).days + 1}일)\n\n"
        f"[캠퍼스 일정]\n{schedule}\n\n"
        f"[지난주 운동 수행]\n{history}\n\n"
        f"[사용자 정보]\n{user_info}"
    )
