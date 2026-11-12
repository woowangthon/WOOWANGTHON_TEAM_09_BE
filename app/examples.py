# 스웨거(/docs) 요청 본문 예시. api.py에서 Body(openapi_examples=...)로 붙인다.
# (모델의 json_schema_extra에 넣으면 Pydantic이 키를 알파벳순으로 정렬해 순서가 뒤섞이므로 여기에 둔다)
#
# 모든 예시는 한 사용자의 상황으로 맞춘다:
#   cbnu2026, 21세 남성, 175cm 70kg, 목표 체중 감량, 헬스 3개월, 헬스장 이용
#   매주 목 09:00~12:00 자료구조 / 2026-11-17(화) 18:00~20:00 동아리 모임 / 매일 취침 23:00~다음 날 07:00, 점심 12:00~13:00 방해 금지
#   계획할 주: 2026-11-16(월) ~ 2026-11-22(일)

INFO_FULL = {
    "age": 21, "height": 175, "weight": 70, "sex": True,
    "experience": "헬스 3개월",
    "nickname": "근육왕", "goal": "체중 감량", "uses_gym": True,
    "busy_times": [{"name": "취침", "start": "23:00", "end": "07:00"}, {"name": "점심", "start": "12:00", "end": "13:00"}],
}

USER_CREATE = {
    "with_info": {
        "summary": "건강 정보와 함께 가입",
        "value": {"name": "cbnu2026", "password": "password123", "department": "소프트웨어학부", "info": INFO_FULL},
    },
    "account_only": {
        "summary": "계정만 가입 (건강 정보는 나중에 POST /info)",
        "value": {"name": "cbnu2026", "password": "password123", "department": "소프트웨어학부"},
    },
    "no_department": {
        "summary": "학과 없이 가입 (대시보드에서 '기타')",
        "value": {"name": "cbnu2026", "password": "password123", "department": None},
    },
}

LOGIN = {
    "login": {"summary": "로그인", "value": {"name": "cbnu2026", "password": "password123"}},
}

INFO = {
    "first": {
        "summary": "처음 등록 (age, height, weight, sex 필수)",
        "value": INFO_FULL,
    },
    "inbody": {
        "summary": "인바디만 입력 (측정 몸무게 함께)",
        "value": {"weight": 72, "body_fat_percent": 25.3, "skeletal_muscle_kg": 31.2, "inbody_measured_at": "2026-11-10"},
    },
    "busy_times": {
        "summary": "방해 금지 시간대 바꾸기 (목록 전체를 보냄. 다음 날로 넘어가도 됨)",
        "value": {"busy_times": [
            {"name": "점심", "start": "12:00", "end": "13:00"},
            {"name": "저녁", "start": "18:00", "end": "19:00"},
            {"name": "직접 설정 3", "start": "14:00", "end": "15:00"},
        ]},
    },
    "clear": {
        "summary": "선택 항목 지우기 (null)",
        "value": {"experience": None, "busy_times": []},
    },
}

PLAN = {
    "new": {"summary": "주간 계획 생성", "value": {"goal": "체중 감량", "week": "2026-11-16"}},
    "info_goal": {"summary": "목표 생략 (POST /info의 goal 사용)", "value": {"week": "2026-11-16"}},
    "regenerate": {"summary": "다른 계획 추천 (오늘 이후 AI 운동 일정을 교체)",
                   "value": {"week": "2026-11-16", "regenerate": True}},
}

SCHEDULE = {
    "kst": {
        "summary": "일정 추가 (시간대 없이 = 한국 시간)",
        "value": {"name": "동아리 모임", "start_term": "2026-11-17T18:00", "end_term": "2026-11-17T20:00"},
    },
    "with_offset": {
        "summary": "시간대를 붙여 보내기",
        "value": {"name": "중간고사", "start_term": "2026-11-20T10:00:00+09:00", "end_term": "2026-11-20T11:30:00+09:00"},
    },
}

WEEKLY_BLOCK = {
    "class": {
        "summary": "매주 목 09:00~12:00 수업",
        "value": {"weekday": 4, "start_time": "09:00", "end_time": "12:00", "name": "자료구조"},
    },
}

WORKOUT_EXERCISE = {
    "done": {"summary": "완료 체크", "value": {"completed": True}},
    "undo": {"summary": "완료 취소", "value": {"completed": False}},
}
