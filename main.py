# 서버 진입점. 실행: uvicorn main:app --reload
#
# 코드는 app/ 패키지에 있다. 구조와 요청 처리 흐름은 README.md 참고.
# API 문서: 서버 실행 후 /docs (스웨거), /redoc, /openapi.json

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute

from app.api import router
from app.config import CORS_ORIGINS

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")

API_DESCRIPTION = """
충북대 재학생(청소년~중장년)의 **캠퍼스 일정을 고려해 주간 헬스 운동 계획을 만들어 주는** API.
운동 계획은 운동 지침 문서(WHO, 대한비만학회, 신체활동 지침서 등)를 근거로 AI가 만든다.

## 사용 순서
1. `POST /user` 회원가입 (학과·건강 정보를 함께 보낼 수 있음. 인바디는 `POST /inbody/image`로 사진에서 읽을 수 있음, 로그인 불필요)
2. `POST /login` → `access_token` 받기
3. `POST /info` 건강 정보·인바디 등록 (가입 때 안 보냈다면 필수)
4. `POST /weekly-block` 매주 반복 수업·운동 불가 시간 등록, `POST /schedule` 그 주의 일회성 일정 등록
5. `POST /plan` 주간 운동 계획 생성 → 운동하는 날이 일정에 자동 저장 (`regenerate: true`면 다른 계획으로 교체, `DELETE /plan`으로 그 주 플랜 삭제)
6. `PUT /workout-exercise/{id}` 운동한 종목 이수 체크
7. `GET /report` 요일별 수행률 확인 → 다음 주 `POST /plan`에 지난주 수행률이 반영됨
8. `GET /dashboard` 단과대학별 수행률 순위

## 인증
- `/`, `POST /user`, `POST /login`, `POST /inbody/image`를 뺀 모든 API는 헤더 `Authorization: Bearer <access_token>`이 필요하다.
- 토큰이 없거나 잘못됐거나 만료되면 **401** → 다시 로그인한다.
- 모든 데이터는 로그인한 본인 것만 보이고 바뀐다. 다른 사람의 id를 넣으면 **404**.
- 사용자의 내부 id는 응답에 나오지 않는다. 사용자는 로그인 아이디(`name`)로만 구분한다.

## 날짜·시간 형식 (모두 한국 시간)
| 종류 | 형식 | 예 |
|---|---|---|
| 날짜 | `YYYY-MM-DD` | `2026-11-17` |
| 하루 안 시각 | `HH:mm` (24시간제) | `19:00` |
| 일시 (응답) | `YYYY-MM-DDTHH:MM:SS` (시간대 표기 없음) | `2026-11-17T19:00:00` |
| 일시 (요청) | 시간대 없이 보내면 한국 시간으로 본다. `+09:00`, `Z`를 붙여도 됨 | `2026-11-17T19:00` |

- 주는 **월요일~일요일**이다. `week` 파라미터에는 그 주의 아무 날짜나 넣으면 된다.
- 일정 겹침은 `[시작, 끝)` 기준이라 끝과 시작이 맞닿는 것(10:00 끝, 10:00 시작)은 겹침이 아니다.

## 오류 응답 형식
| 상태 | `detail` 형식 | 언제 |
|---|---|---|
| 400, 401, 404, 500, 502 | 문자열 (사용자에게 보여줘도 되는 한국어) | 대부분의 오류 |
| 409 | 문자열 (아이디 중복) 또는 `{"message": 문자열, "conflicts": [...]}` (일정 겹침) | 각 API 설명 참고 |
| 422 | FastAPI 기본 형식: `[{"loc": [...], "msg": ..., "type": ...}]` 배열. `POST /info`의 필수값 누락만 문자열 | 요청 형식·값 범위 오류 |
"""

OPENAPI_TAGS = [
    {"name": "인증·사용자", "description": "회원가입, 로그인(JWT 발급), 내 정보"},
    {"name": "건강 정보", "description": "나이·키·몸무게·성별·헬스 경력·인바디. 운동 계획의 입력이 된다"},
    {"name": "운동 계획", "description": "AI 주간 운동 계획 생성 (월~일), 다른 계획 추천, 플랜 삭제. 결과는 일정에 자동 저장"},
    {"name": "일정", "description": "날짜가 정해진 일정(약속, 시험 등)과 AI 운동 일정. 캘린더 화면용"},
    {"name": "반복 시간표", "description": "매주 반복되는 수업·운동 불가 시간 (요일 + 시각)"},
    {"name": "운동 이수", "description": "AI 운동 일정의 종목별 완료 체크"},
    {"name": "주간 리포트", "description": "요일별 운동 수행률"},
    {"name": "대시보드", "description": "단과대학별 운동 수행률 경쟁 (학과는 가입할 때 입력)"},
    {"name": "기타", "description": "서버 상태 확인"},
]

app = FastAPI(
    title="대학생 맞춤형 헬스 AI 서버",
    version="1.0.0",
    description=API_DESCRIPTION,
    openapi_tags=OPENAPI_TAGS,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,     # 허용할 FE 주소 (app/config.py, 환경변수 CORS_ORIGINS)
    allow_methods=["*"],           # GET, POST, PUT, DELETE ...
    allow_headers=["*"],           # Authorization(Bearer 토큰), Content-Type 포함
)
app.include_router(router)


def openapi_with_null_examples() -> dict:
    # FastAPI는 명세를 만들 때 값이 null인 키를 지운다. 그러면 "값 지우기(null)" 같은 요청 예시가 {}로 보이므로,
    # 만든 뒤 요청 예시(app/examples.py)를 원본 그대로 다시 넣는다.
    if app.openapi_schema:
        return app.openapi_schema
    schema = FastAPI.openapi(app)
    for route in router.routes:
        examples = getattr(route.body_field.field_info, "openapi_examples", None) if (
            isinstance(route, APIRoute) and route.body_field is not None) else None
        for method in (route.methods if examples else ()):
            schema["paths"][route.path][method.lower()]["requestBody"]["content"]["application/json"]["examples"] = examples
    return schema


app.openapi = openapi_with_null_examples
