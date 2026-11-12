# HTTP 엔드포인트. 요청을 받아 planner·auth·db에 넘기고, 오류를 HTTP 응답으로 바꾸기만 한다.
# 스웨거(/docs) 문서: 각 엔드포인트의 summary·description·responses, 응답 형식은 responses.py, 요청 형식은 schemas.py.

import logging
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Response, UploadFile, status

from app import examples

from app.auth import create_access_token, current_user_id, hash_password, verify_password
from app.db import (
    BlockConflictError, IdentityColumnError, NameTakenError, ScheduleConflictError, delete_ai_workouts,
    delete_weekly_block, get_weekly_block, insert_weekly_block, list_weekly_blocks, update_weekly_block,
    delete_schedule, fetch_login, fetch_user, fetch_user_info, get_schedule, insert_schedule, insert_user,
    list_schedules, list_workout_exercises, set_workout_exercise_completed, update_schedule, upsert_user_info,
)
from app.inbody import extract_inbody
from app.ocr import ImageError, read_lines
from app.planner import PlanError, create_plan
from app.departments import college_of
from app.report import college_dashboard, weekly_report
from app.responses import (
    BlockConflictOut, DashboardOut, ErrorOut, InbodyImageOut, InfoOut, PlanConflictOut, PlanDeleteOut, PlanOut, ReportOut, ScheduleConflictOut,
    ScheduleDetailOut, ScheduleOut, TokenOut, UserOut, WeeklyBlockOut, WorkoutExerciseOut,
)
from app.schemas import (
    REQUIRED_INFO, InfoUpdate, LoginRequest, PlanRequest, ScheduleIn, UserCreate, WeeklyBlockIn, WorkoutExerciseUpdate,
)
from app.week import today, week_bounds

router = APIRouter()
logger = logging.getLogger(__name__)

# ---------- 스웨거 공통 오류 응답 ----------
AUTH_ERROR = {401: {"model": ErrorOut, "description": "토큰이 없거나 잘못됐거나 만료됨 → 다시 로그인"}}


def not_found(what: str) -> dict:
    return {404: {"model": ErrorOut, "description": f"{what}이(가) 없거나 본인 것이 아님"}}


TAG_AUTH, TAG_INFO, TAG_PLAN = "인증·사용자", "건강 정보", "운동 계획"
TAG_SCHEDULE, TAG_BLOCK, TAG_DONE, TAG_REPORT = "일정", "반복 시간표", "운동 이수", "주간 리포트"
TAG_DASHBOARD = "대시보드"


@router.get(
    "/",
    tags=["기타"],
    summary="서버 상태 확인",
    description="서버가 켜져 있으면 {\"Hello\": \"World\"}.",
    responses={200: {"content": {"application/json": {"example": {"Hello": "World"}}}}},
)
def read_root():

    # response = client.chat.completions.create(
    #     model="claude-sonnet-5",
    #     messages=[{"role": "user", "content": "Hello!"}],
    # )
    # print(response.choices[0].message.content)

    return {"Hello": "World"}


# ---------- 인증·사용자 ----------

@router.post(
    "/user",
    tags=[TAG_AUTH],
    summary="회원가입",
    status_code=status.HTTP_201_CREATED,
    response_model=UserOut,
    description=(
        "아이디(`name`)와 비밀번호로 가입한다. 토큰은 주지 않으므로 이어서 `POST /login`을 호출한다.\n\n"
        "- `name`: 영문 소문자로 시작, 영문 소문자·숫자 4~20자, 중복 불가\n"
        "- `password`: 8~128자. 서버에는 Argon2id 해시로만 저장된다\n"
        "- `department`(선택): 충북대 학과명 (예: `소프트웨어학부`). 단과대학(`college`)은 서버가 학과로 채운다. "
        "목록에 없는 이름이면 422. 기타(학과 없음)면 생략하거나 null → 대시보드에서 '기타'\n"
        "- `info`(선택): 건강 정보를 함께 보내면 가입과 동시에 저장한다 (`age`, `height`, `weight`, `sex` 필수). "
        "생략하면 나중에 `POST /info`로 등록한다"
    ),
    responses={409: {"model": ErrorOut, "description": "이미 사용 중인 아이디"}},
)
def create_user(body: Annotated[UserCreate, Body(openapi_examples=examples.USER_CREATE)]):
    try:
        user = insert_user(body.name, hash_password(body.password), body.department, college_of(body.department))
    except NameTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 아이디입니다.")
    user_id = user.pop("id")
    info = None
    if body.info is not None:
        info = upsert_user_info(user_id, body.info.model_dump(mode="json"))
    return {**user, "info": info}


@router.post(
    "/login",
    tags=[TAG_AUTH],
    summary="로그인 (JWT 발급)",
    response_model=TokenOut,
    description=(
        "아이디·비밀번호가 맞으면 JWT를 준다. 이후 로그인이 필요한 모든 요청에 "
        "`Authorization: Bearer <access_token>` 헤더를 넣는다.\n\n"
        "토큰 유효 기간은 서버 설정(`JWT_EXPIRE_MINUTES`, 현재 1주일)을 따른다. 만료되면 401이 오므로 다시 로그인한다."
    ),
    responses={401: {"model": ErrorOut, "description": "아이디 또는 비밀번호가 틀림 (어느 쪽인지는 알려주지 않음)"}},
)
def login(body: Annotated[LoginRequest, Body(openapi_examples=examples.LOGIN)]):
    user = fetch_login(body.name)
    if not verify_password(user["password"] if user else None, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"access_token": create_access_token(user["id"]), "token_type": "bearer"}


@router.get(
    "/user",
    tags=[TAG_AUTH],
    summary="내 정보 조회",
    response_model=UserOut,
    description="로그인한 본인의 아이디, 가입 시각, 학과·단과대학, 건강 정보. 건강 정보를 아직 등록하지 않았으면 `info`는 null.",
    responses={**AUTH_ERROR, **not_found("사용자")},
)
def read_me(user_id: int = Depends(current_user_id)):
    user = fetch_user(user_id)
    if user is None:   # 토큰은 유효하지만 탈퇴 등으로 사용자가 없는 경우
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.")
    return {**user, "info": fetch_user_info(user_id)}


# ---------- 건강 정보 ----------

@router.post(
    "/info",
    tags=[TAG_INFO],
    summary="건강 정보·인바디 저장 (보낸 필드만 수정)",
    response_model=InfoOut,
    description=(
        "나이, 키, 몸무게, 성별, 헬스 경력, 인바디(체지방률·골격근량·측정일), "
        "닉네임, 운동 목표, 헬스장 이용 여부, 방해 금지 시간대를 저장한다.\n\n"
        "- **처음 등록**: `age`, `height`, `weight`, `sex` 필수\n"
        "- **그다음**: 바꿀 필드만 보낸다. 보내지 않은 필드는 그대로 유지된다\n"
        "- 선택 항목(`experience`, 인바디 값)을 지우려면 그 필드를 `null`로 보낸다\n"
        "- 인바디를 입력할 때는 측정 몸무게를 `weight`로 함께 보내면 BMI에 반영된다\n"
        "- `goal`: `POST /plan`에서 goal을 생략하면 이 값을 쓴다 / `uses_gym`: false면 기구 없이 할 수 있는 운동 위주로 계획\n"
        "- `busy_times`: **매일** 방해받고 싶지 않은 시간대 목록 `[{\"name\": \"취침\", \"start\": \"23:00\", \"end\": \"07:00\"}, ...]` "
        "(최대 10개, `name`은 생략 가능). 보내면 **목록 전체를 바꾼다** (하나만 추가·삭제할 때도 전체 목록을 보낸다). "
        "모두 지우려면 `[]`. 끝이 시작보다 이르면 다음 날 시각이다 (23:00~07:00 = 밤 11시~다음 날 아침 7시). "
        "`POST /plan`이 이 시간들을 피한다\n"
        "- 요일마다 다른 시간(수업 시간표)은 `POST /weekly-block`, 날짜가 정해진 일정은 `POST /schedule`로 등록한다\n\n"
        "응답은 저장된 전체 건강 정보다. 이 값들은 `POST /plan`에서 운동 계획을 짤 때 쓰인다."
    ),
    responses={
        **AUTH_ERROR,
        422: {"description": "값 범위 오류, 필수 필드를 null로 보냄, 측정일이 미래, 시각 형식 오류, "
                             "방해 금지 시간대의 시작과 끝이 같음·11개 이상, 또는 (detail이 문자열) 처음 등록인데 필수 필드가 빠짐"},
        500: {"model": ErrorOut, "description": "DB 설정 오류"},
    },
)
def save_info(
    body: Annotated[InfoUpdate, Body(openapi_examples=examples.INFO)], user_id: int = Depends(current_user_id),
):
    changes = body.model_dump(mode="json", exclude_unset=True)   # 보낸 필드만
    merged = {**(fetch_user_info(user_id) or {}), **changes}
    if missing := [k for k in REQUIRED_INFO if merged.get(k) is None]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"처음 등록할 때는 {', '.join(missing)}가 필요합니다.",
        )
    try:
        return upsert_user_info(user_id, merged)
    except IdentityColumnError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DB 설정 오류: info.id가 자동 증가(identity) 열이라 사용자 id를 넣을 수 없습니다.",
        )


MAX_IMAGE_BYTES = 10 * 1024 * 1024   # 인바디 결과지 사진 최대 10MB


@router.post(
    "/inbody/image",
    tags=[TAG_INFO],
    summary="인바디 결과지 사진 읽기 (OCR)",
    response_model=InbodyImageOut,
    description=(
        "인바디 결과지 사진(JPG, PNG 등, 10MB 이하)을 `multipart/form-data`의 `file`로 올리면 "
        "OCR로 글자를 읽어 체지방률, 골격근량, 측정일을 찾아 돌려준다.\n\n"
        "- **저장하지 않는다.** OCR은 틀릴 수 있으므로 사용자에게 값을 보여 주고 확인·수정받은 뒤 "
        "`POST /info`로 저장한다. 응답의 키 이름이 `/info`와 같아 그대로 보내면 된다\n"
        "- 찾지 못한 값은 null이고 `missing`에 들어간다 → 그 칸만 직접 입력받는다\n"
        "- `warnings`가 있으면 사용자에게 보여 준다 (예: 결과지 두 곳의 값이 다름)\n"
        "- 결과지 전체가 나오고 반듯하게 찍힌 사진일수록 정확하다 (약 ±6도 기울기, 휴대폰 회전 정보까지 처리)\n"
        "- 오픈소스 OCR(RapidOCR)을 서버에서 실행하므로 수 초~십수 초 걸린다. 한 번에 한 장씩 처리한다\n\n"
        "InBody 270 결과지 기준으로 만들었다. 결과지 안의 그래프 값과 신체변화 표(최근 값)를 서로 대조한다.\n\n"
        "**로그인 없이 쓸 수 있다** (회원가입 화면에서 인바디를 사진으로 입력받을 수 있도록). 토큰을 보내도 무시한다."
    ),
    responses={
        400: {"model": ErrorOut, "description": "이미지가 아니거나 읽을 수 없음"},
        413: {"model": ErrorOut, "description": "10MB 초과"},
        503: {"model": ErrorOut, "description": "OCR 엔진을 준비하지 못함 (모델 파일 문제) → 잠시 후 다시 시도"},
    },
)
def read_inbody_image(
    file: UploadFile = File(description="인바디 결과지 사진 (JPG, PNG 등, 10MB 이하)"),
):
    data = file.file.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="사진은 10MB 이하로 올려 주세요.")
    try:
        lines = read_lines(data)
    except ImageError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except (ImportError, OSError, RuntimeError) as e:   # 모델 파일을 못 받았거나 엔진을 만들지 못함
        logger.exception("OCR 엔진 오류")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="사진 읽기 기능을 지금 쓸 수 없습니다. 잠시 후 다시 시도해 주세요.") from e
    return extract_inbody(lines)


# ---------- 운동 계획 ----------

@router.post(
    "/plan",
    tags=[TAG_PLAN],
    summary="주간 운동 계획 생성 (AI) + 일정 저장",
    response_model=PlanOut,
    description=(
        "`week`가 속한 주(월~일)의 운동 계획을 AI가 만들고, 운동하는 날을 일정(`schedule`)에 저장한다.\n\n"
        "**입력으로 쓰는 것**\n"
        "- 요청 본문: `goal`(운동 목표. 없으면 `POST /info`의 goal), `week`(그 주의 아무 날짜. 없으면 이번 주), "
        "`regenerate`(다른 계획 추천)\n"
        "- DB: 건강 정보(`POST /info`, 헬스장 이용 여부·방해 금지 시간대 `busy_times` 포함), 그 주의 일정(`/schedule`), 매주 반복 시간표(`/weekly-block`), "
        "지난주 운동 수행률(`/workout-exercise`의 이수 기록)\n\n"
        "**다른 계획 추천 (`\"regenerate\": true`)**\n"
        "- 그 주의 오늘 이후 AI 운동 일정을 AI에게 '이전 계획'으로 보여 주고, 운동 구성·요일·시간을 다르게 다시 짠다\n"
        "- 새 계획이 저장된 뒤에만 이전 일정(과 그 이수 기록)을 지운다. 실패하면 이전 일정은 그대로 남는다\n"
        "- 지운 개수는 응답의 `replaced`\n"
        "**동작**\n"
        "- 이미 지난 요일은 빼고 짠다 (수요일에 이번 주를 요청하면 수~일)\n"
        "- 운동 시간은 08:00~22:00 안에서 일정·반복 시간표·방해 금지 시간대와 겹치지 않게 정한다. "
        "AI가 겹치게 짜면 서버가 그날 가장 가까운 빈 시간으로 옮기고, 빈 시간이 없으면 그날을 휴식으로 바꾼다 (`adjusted`에 기록)\n"
        "- BMI 25 이상이면 고강도 운동을 뺀다\n"
        "- 운동하는 날은 `schedule`에 `created_by_ai: true`로, 그날의 운동은 `saved[].exercises`에 저장된다. "
        "`saved[].exercises[].id`로 `PUT /workout-exercise/{id}` 이수 체크를 한다\n"
        "- `regenerate` 없이 다시 요청하면 이미 저장된 운동 일정은 바쁜 시간으로 취급된다 (계획이 추가로 쌓임). "
        "바꾸려면 `regenerate: true`, 지우려면 `DELETE /plan`\n\n"
        "**주의**: AI를 호출하므로 응답까지 수십 초 걸릴 수 있고 토큰 비용이 든다. 버튼 연타를 막아 주세요."
    ),
    responses={
        **AUTH_ERROR,
        400: {"model": ErrorOut, "description": "건강 정보 미등록 (`POST /info` 먼저), 운동 목표 없음, 또는 이미 지난 주"},
        409: {"model": PlanConflictOut, "description": "계획을 만드는 동안 추가된 일정과 겹침. 아무것도 저장되지 않음 → 다시 요청"},
        502: {"model": ErrorOut, "description": "AI 응답 형식 오류. 아무것도 저장되지 않음 → 다시 요청"},
    },
)
def plan(req: Annotated[PlanRequest, Body(openapi_examples=examples.PLAN)], user_id: int = Depends(current_user_id)):
    try:
        return create_plan(req, user_id)
    except PlanError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.delete(
    "/plan",
    tags=[TAG_PLAN],
    summary="운동 플랜 삭제 (그 주 전체)",
    response_model=PlanDeleteOut,
    description=(
        "`week`가 속한 주(월~일)의 AI 운동 일정을 모두 지운다. 그 일정의 운동 목록과 이수 기록도 함께 지워져 "
        "주간 리포트에서도 사라진다 (지난 요일 포함). 사용자가 직접 넣은 일정과 반복 시간표는 그대로.\n\n"
        "그 주에 플랜이 없으면 `deleted: 0` (오류 아님)."
    ),
    responses=AUTH_ERROR,
)
def delete_plan(
    user_id: int = Depends(current_user_id),
    week: date | None = Query(None, description="이 날짜가 속한 주 (YYYY-MM-DD, 아무 요일이나). 없으면 이번 주"),
):
    monday, sunday = week_bounds(week or today())
    deleted = delete_ai_workouts(user_id, monday, sunday)
    return {"week_start": monday.isoformat(), "week_end": sunday.isoformat(), "deleted": deleted}


# ---------- 일정 (모두 로그인 필요, 본인 일정만) ----------

SCHEDULE_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="일정을 찾을 수 없습니다.")
SCHEDULE_CONFLICT_DOC = {409: {"model": ScheduleConflictOut,
                               "description": "같은 사용자의 다른 일정과 시간이 겹침. detail.conflicts에 겹치는 일정"}}


def schedule_conflict(e: ScheduleConflictError) -> HTTPException:
    # 409. detail에 겹치는 일정 목록을 담아, 앱에서 어떤 일정과 겹치는지 보여줄 수 있게 한다.
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"message": "다른 일정과 시간이 겹칩니다.", "conflicts": e.conflicts},
    )


@router.get(
    "/schedule",
    tags=[TAG_SCHEDULE],
    summary="내 일정 목록",
    response_model=list[ScheduleOut],
    description=(
        "본인 일정(사용자가 넣은 일정 + AI 운동 일정)을 시작 시각 순으로. "
        "`start`·`end`를 주면 그 기간과 겹치는 일정만 (캘린더 화면용).\n\n"
        "운동 목록은 포함되지 않는다. 운동 일정의 운동과 이수 여부는 `GET /schedule/{id}`로 본다."
    ),
    responses={**AUTH_ERROR, 400: {"model": ErrorOut, "description": "end가 start보다 빠름"}},
)
def read_schedules(
    user_id: int = Depends(current_user_id),
    start: date | None = Query(None, description="이 날짜(포함) 이후와 겹치는 일정만 (YYYY-MM-DD)"),
    end: date | None = Query(None, description="이 날짜(포함) 이전과 겹치는 일정만 (YYYY-MM-DD)"),
):
    if start and end and end < start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end는 start보다 빠를 수 없습니다.")
    return list_schedules(user_id, start, end)


@router.get(
    "/schedule/{schedule_id}",
    tags=[TAG_SCHEDULE],
    summary="일정 하나 (운동 일정이면 운동 목록·이수 여부 포함)",
    response_model=ScheduleDetailOut,
    description="AI 운동 일정(`created_by_ai: true`)이면 `exercises`에 그날의 운동과 운동별 이수 여부가 있다. "
                "사용자 일정이면 `exercises`는 빈 배열.",
    responses={**AUTH_ERROR, **not_found("일정")},
)
def read_schedule(schedule_id: int, user_id: int = Depends(current_user_id)):
    schedule = get_schedule(user_id, schedule_id)
    if schedule is None:
        raise SCHEDULE_NOT_FOUND
    schedule["exercises"] = list_workout_exercises(schedule_id) if schedule["created_by_ai"] else []
    return schedule


@router.post(
    "/schedule",
    tags=[TAG_SCHEDULE],
    summary="일정 추가 (한 번만 있는 일정)",
    status_code=status.HTTP_201_CREATED,
    response_model=ScheduleOut,
    description=(
        "약속, 시험처럼 날짜가 정해진 일정을 추가한다. 매주 반복되는 수업은 `POST /weekly-block`을 쓴다.\n\n"
        "시각에 시간대가 없으면(예: `2026-11-10T09:00`) 한국 시간으로 본다. "
        "같은 사용자의 다른 일정과 겹치면 409 (끝과 시작이 맞닿는 것은 허용)."
    ),
    responses={**AUTH_ERROR, **SCHEDULE_CONFLICT_DOC},
)
def create_schedule(
    body: Annotated[ScheduleIn, Body(openapi_examples=examples.SCHEDULE)], user_id: int = Depends(current_user_id),
):
    try:
        return insert_schedule(user_id, body.name, body.start_term, body.end_term)
    except ScheduleConflictError as e:
        raise schedule_conflict(e)


@router.put(
    "/schedule/{schedule_id}",
    tags=[TAG_SCHEDULE],
    summary="일정 수정 (전체 교체)",
    response_model=ScheduleOut,
    description="`name`, `start_term`, `end_term`을 모두 보낸다. 자기 자신을 뺀 다른 일정과 겹치면 409. "
                "AI 운동 일정의 시간을 옮겨도 운동 목록과 이수 기록은 유지된다.",
    responses={**AUTH_ERROR, **not_found("일정"), **SCHEDULE_CONFLICT_DOC},
)
def replace_schedule(
    schedule_id: int,
    body: Annotated[ScheduleIn, Body(openapi_examples=examples.SCHEDULE)],
    user_id: int = Depends(current_user_id),
):
    try:
        schedule = update_schedule(user_id, schedule_id, body.name, body.start_term, body.end_term)
    except ScheduleConflictError as e:
        raise schedule_conflict(e)
    if schedule is None:
        raise SCHEDULE_NOT_FOUND
    return schedule


@router.delete(
    "/schedule/{schedule_id}",
    tags=[TAG_SCHEDULE],
    summary="일정 삭제",
    status_code=status.HTTP_204_NO_CONTENT,
    description="응답 본문 없음. AI 운동 일정을 지우면 그날의 운동과 이수 기록도 함께 지워진다.",
    responses={**AUTH_ERROR, **not_found("일정")},
)
def remove_schedule(schedule_id: int, user_id: int = Depends(current_user_id)):
    if not delete_schedule(user_id, schedule_id):
        raise SCHEDULE_NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 운동 이수 (AI가 만든 운동 일정의 운동만) ----------

@router.put(
    "/workout-exercise/{exercise_id}",
    tags=[TAG_DONE],
    summary="운동 하나 이수 체크 / 해제",
    response_model=WorkoutExerciseOut,
    description=(
        "`exercise_id`는 `POST /plan` 응답의 `saved[].exercises[].id` 또는 `GET /schedule/{id}`의 `exercises[].id`.\n\n"
        "`{\"completed\": true}`면 완료, `false`면 취소. 이 기록으로 주간 리포트(`GET /report`)를 계산하고, "
        "다음 주 계획(`POST /plan`)에 지난주 수행률로 반영한다."
    ),
    responses={**AUTH_ERROR, **not_found("운동")},
)
def update_workout_exercise(
    exercise_id: int,
    body: Annotated[WorkoutExerciseUpdate, Body(openapi_examples=examples.WORKOUT_EXERCISE)],
    user_id: int = Depends(current_user_id),
):
    exercise = set_workout_exercise_completed(user_id, exercise_id, body.completed)
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="운동을 찾을 수 없습니다.")
    return exercise


# ---------- 매주 반복 일정 (수업 시간표, 요일별 운동 불가 시간) ----------

BLOCK_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="반복 일정을 찾을 수 없습니다.")
BLOCK_CONFLICT_DOC = {409: {"model": BlockConflictOut,
                            "description": "같은 요일의 다른 반복 일정과 시간이 겹침. detail.conflicts에 겹치는 반복 일정"}}


def block_conflict(e: BlockConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"message": "같은 요일의 다른 반복 일정과 시간이 겹칩니다.", "conflicts": e.conflicts},
    )


@router.get(
    "/weekly-block",
    tags=[TAG_BLOCK],
    summary="반복 시간표 목록",
    response_model=list[WeeklyBlockOut],
    description="본인의 매주 반복 일정(수업, 아르바이트 등 운동할 수 없는 시간). 요일, 시작 시각 순.",
    responses=AUTH_ERROR,
)
def read_weekly_blocks(user_id: int = Depends(current_user_id)):
    return list_weekly_blocks(user_id)


@router.get(
    "/weekly-block/{block_id}",
    tags=[TAG_BLOCK],
    summary="반복 일정 하나",
    response_model=WeeklyBlockOut,
    responses={**AUTH_ERROR, **not_found("반복 일정")},
)
def read_weekly_block(block_id: int, user_id: int = Depends(current_user_id)):
    block = get_weekly_block(user_id, block_id)
    if block is None:
        raise BLOCK_NOT_FOUND
    return block


@router.post(
    "/weekly-block",
    tags=[TAG_BLOCK],
    summary="반복 일정 추가 (예: 매주 목 09:00~12:00 자료구조)",
    status_code=status.HTTP_201_CREATED,
    response_model=WeeklyBlockOut,
    description=(
        "수업 시간표나 요일별로 운동할 수 없는 시간을 등록한다. `POST /plan`이 이 시간을 피해서 운동을 잡는다.\n\n"
        "`weekday`: 1=월 … 7=일. 시각은 `HH:mm`(24시간제). 같은 요일의 다른 반복 일정과 겹치면 409 (맞닿는 것은 허용)."
    ),
    responses={**AUTH_ERROR, **BLOCK_CONFLICT_DOC},
)
def create_weekly_block(
    body: Annotated[WeeklyBlockIn, Body(openapi_examples=examples.WEEKLY_BLOCK)], user_id: int = Depends(current_user_id),
):
    try:
        return insert_weekly_block(user_id, body.weekday, body.start_time, body.end_time, body.name)
    except BlockConflictError as e:
        raise block_conflict(e)


@router.put(
    "/weekly-block/{block_id}",
    tags=[TAG_BLOCK],
    summary="반복 일정 수정 (전체 교체)",
    response_model=WeeklyBlockOut,
    description="`weekday`, `start_time`, `end_time`, `name`을 모두 보낸다. 자기 자신을 뺀 같은 요일의 반복 일정과 겹치면 409.",
    responses={**AUTH_ERROR, **not_found("반복 일정"), **BLOCK_CONFLICT_DOC},
)
def replace_weekly_block(
    block_id: int,
    body: Annotated[WeeklyBlockIn, Body(openapi_examples=examples.WEEKLY_BLOCK)],
    user_id: int = Depends(current_user_id),
):
    try:
        block = update_weekly_block(user_id, block_id, body.weekday, body.start_time, body.end_time, body.name)
    except BlockConflictError as e:
        raise block_conflict(e)
    if block is None:
        raise BLOCK_NOT_FOUND
    return block


@router.delete(
    "/weekly-block/{block_id}",
    tags=[TAG_BLOCK],
    summary="반복 일정 삭제",
    status_code=status.HTTP_204_NO_CONTENT,
    description="응답 본문 없음.",
    responses={**AUTH_ERROR, **not_found("반복 일정")},
)
def remove_weekly_block(block_id: int, user_id: int = Depends(current_user_id)):
    if not delete_weekly_block(user_id, block_id):
        raise BLOCK_NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 주간 리포트 ----------

@router.get(
    "/report",
    tags=[TAG_REPORT],
    summary="주간 운동 리포트 (요일별 수행률)",
    response_model=ReportOut,
    description=(
        "`week`가 속한 주(월~일)의 AI 운동 일정에 대해, 종목별 이수 여부로 요일별 수행률을 계산한다.\n\n"
        "- 수행률(`rate`) = 완료 종목 수 / 계획 종목 수 × 100 (반올림, 정수 %). 계획이 없는 날은 null\n"
        "- `total`: 주 전체 (앞으로 할 운동 포함) / `so_far`: 오늘까지의 운동만 → 주 중간에는 `so_far`가 실제 진행률\n"
        "- 사용자가 직접 넣은 일정은 포함하지 않는다"
    ),
    responses=AUTH_ERROR,
)
def read_report(
    user_id: int = Depends(current_user_id),
    week: date | None = Query(None, description="이 날짜가 속한 주 (YYYY-MM-DD, 아무 요일이나). 없으면 이번 주"),
):
    return weekly_report(user_id, week or today())


# ---------- 대시보드 (단과대학 경쟁) ----------

@router.get(
    "/dashboard",
    tags=[TAG_DASHBOARD],
    summary="단과대학별 운동 수행률 순위",
    response_model=DashboardOut,
    description=(
        "`week`가 속한 주(월~일)에 단과대학별로 AI 운동 일정의 수행률을 비교한다.\n\n"
        "- 수행률(`rate`) = 소속 학생들의 완료 종목 수 합 / 계획 종목 수 합 × 100 (소수 첫째 자리)\n"
        "- **오늘까지의 운동만 센다** (앞으로 할 운동 때문에 주 중간에 수행률이 낮아 보이지 않도록). 지난 주는 주 전체\n"
        "- 단과대학은 가입할 때 받은 학과로 정해진다. 학과가 없는 사용자는 '기타'로 묶고 순위에는 넣지 않는다\n"
        "- 계획이 하나도 없는 단과대학은 `rank`, `rate`가 null이고 목록 뒤에 온다\n"
        "- 운동한 학생 수: 그 기간(오늘까지)에 한 종목이라도 완료한 학생. 전체는 `active_users`, 단과대학별은 `active_members` "
        "(예: 'nn명이 운동을 수행했어요!')\n"
        "- 다른 사용자의 개인 정보(아이디 등)는 나오지 않는다\n\n"
        "**화면에 쓰는 법**: 'nn명이 운동을 수행했어요!' → `active_users` / 순위표 → `colleges`를 순서대로 "
        "(`rank`가 null이면 '-'로 표시) / 내 단과대학 강조 → `college == my_college`"
    ),
    responses={
        200: {"content": {"application/json": {"example": examples.DASHBOARD_RESPONSE}},
              "description": "단과대학 순위 (예시는 일부 단과대학만 보임. 실제로는 모든 단과대학 + 기타)"},
        **AUTH_ERROR,
        422: {"description": "week 형식 오류 (YYYY-MM-DD)"},
    },
)
def read_dashboard(
    user_id: int = Depends(current_user_id),
    week: date | None = Query(None, description="이 날짜가 속한 주 (YYYY-MM-DD, 아무 요일이나). 없으면 이번 주"),
):
    user = fetch_user(user_id)
    return college_dashboard(week or today(), user["college"] if user else None)
