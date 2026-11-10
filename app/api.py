# HTTP 엔드포인트. 요청을 받아 planner·auth·db에 넘기고, 오류를 HTTP 응답으로 바꾸기만 한다.

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth import create_access_token, current_user_id, hash_password, verify_password
from app.db import (
    BlockConflictError, IdentityColumnError, NameTakenError, ScheduleConflictError,
    delete_weekly_block, get_weekly_block, insert_weekly_block, list_weekly_blocks, update_weekly_block,
    delete_schedule, fetch_login, fetch_user, fetch_user_info, get_schedule, insert_schedule, insert_user,
    list_schedules, list_workout_exercises, set_workout_exercise_completed, update_schedule, upsert_user_info,
)
from app.planner import PlanError, create_plan
from app.report import weekly_report
from app.schemas import (
    REQUIRED_INFO, InfoUpdate, LoginRequest, PlanRequest, ScheduleIn, UserCreate, WeeklyBlockIn, WorkoutExerciseUpdate,
)
from app.week import today

router = APIRouter()


@router.get("/")
def read_root():

    # response = client.chat.completions.create(
    #     model="claude-sonnet-5",
    #     messages=[{"role": "user", "content": "Hello!"}],
    # )
    # print(response.choices[0].message.content)

    return {"Hello": "World"}


@router.post(
    "/plan",
    description=(
        "로그인한 본인의 건강 정보(DB의 info)와 일정(DB의 schedule)을 바탕으로 기간의 운동 계획을 만들고, "
        "운동하는 날을 schedule에 저장한다. 운동 시간은 기존 일정과 겹치지 않는다.\n"
        "헤더: Authorization: Bearer <토큰>. info가 없으면 400."
    ),
)
def plan(req: PlanRequest, user_id: int = Depends(current_user_id)):
    try:
        return create_plan(req, user_id)
    except PlanError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post(
    "/user",
    status_code=status.HTTP_201_CREATED,
    description=(
        "회원가입. 비밀번호는 Argon2id(무작위 salt 포함) 해시로만 저장한다. 내부 id는 응답에 넣지 않는다.\n"
        "info(나이, 키, 몸무게, 경력)를 함께 보내면 건강 정보도 저장한다."
    ),
)
def create_user(body: UserCreate):
    try:
        user = insert_user(body.name, hash_password(body.password))
    except NameTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 아이디입니다.")
    user_id = user.pop("id")
    info = None
    if body.info is not None:
        info = upsert_user_info(user_id, body.info.model_dump(mode="json"))
    return {**user, "info": info}


@router.post(
    "/login",
    description="아이디·비밀번호가 맞으면 JWT 액세스 토큰을 준다. 아이디와 비밀번호 중 무엇이 틀렸는지는 알려주지 않는다.",
)
def login(body: LoginRequest):
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
    description="로그인한 본인의 정보. 헤더: Authorization: Bearer <토큰>. 건강 정보(info)가 없으면 info는 null.",
)
def read_me(user_id: int = Depends(current_user_id)):
    user = fetch_user(user_id)
    if user is None:   # 토큰은 유효하지만 탈퇴 등으로 사용자가 없는 경우
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.")
    return {**user, "info": fetch_user_info(user_id)}


@router.post(
    "/info",
    description=(
        "로그인한 본인의 건강 정보(나이, 키, 몸무게, 성별, 경력, 인바디)를 저장한다. 보낸 필드만 바꾼다.\n"
        "처음 등록할 때는 age, height, weight, sex가 필수. 헤더: Authorization: Bearer <토큰>."
    ),
)
def save_info(body: InfoUpdate, user_id: int = Depends(current_user_id)):
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



# ---------- 일정 (모두 로그인 필요, 본인 일정만) ----------

SCHEDULE_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="일정을 찾을 수 없습니다.")


def schedule_conflict(e: ScheduleConflictError) -> HTTPException:
    # 409. detail에 겹치는 일정 목록을 담아, 앱에서 어떤 일정과 겹치는지 보여줄 수 있게 한다.
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"message": "다른 일정과 시간이 겹칩니다.", "conflicts": e.conflicts},
    )


@router.get(
    "/schedule",
    description="본인 일정 목록 (시작 시각 순). start·end를 주면 그 기간과 겹치는 일정만.",
)
def read_schedules(
    user_id: int = Depends(current_user_id),
    start: date | None = Query(None, description="이 날짜(포함) 이후와 겹치는 일정만"),
    end: date | None = Query(None, description="이 날짜(포함) 이전과 겹치는 일정만"),
):
    if start and end and end < start:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end는 start보다 빠를 수 없습니다.")
    return list_schedules(user_id, start, end)


@router.get(
    "/schedule/{schedule_id}",
    description="일정 하나. AI가 만든 운동 일정이면 exercises에 그날의 운동과 운동별 이수 여부(completed)가 있다.",
)
def read_schedule(schedule_id: int, user_id: int = Depends(current_user_id)):
    schedule = get_schedule(user_id, schedule_id)
    if schedule is None:
        raise SCHEDULE_NOT_FOUND
    schedule["exercises"] = list_workout_exercises(schedule_id) if schedule["created_by_ai"] else []
    return schedule


@router.post(
    "/schedule",
    status_code=status.HTTP_201_CREATED,
    description="다른 일정과 시간이 겹치면 409.",
)
def create_schedule(body: ScheduleIn, user_id: int = Depends(current_user_id)):
    try:
        return insert_schedule(user_id, body.name, body.start_term, body.end_term)
    except ScheduleConflictError as e:
        raise schedule_conflict(e)


@router.put(
    "/schedule/{schedule_id}",
    description="일정 전체를 바꾼다 (name, start_term, end_term 모두 보냄). 다른 일정과 시간이 겹치면 409.",
)
def replace_schedule(schedule_id: int, body: ScheduleIn, user_id: int = Depends(current_user_id)):
    try:
        schedule = update_schedule(user_id, schedule_id, body.name, body.start_term, body.end_term)
    except ScheduleConflictError as e:
        raise schedule_conflict(e)
    if schedule is None:
        raise SCHEDULE_NOT_FOUND
    return schedule


@router.delete("/schedule/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_schedule(schedule_id: int, user_id: int = Depends(current_user_id)):
    if not delete_schedule(user_id, schedule_id):
        raise SCHEDULE_NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)



# ---------- 운동 이수 (AI가 만든 운동 일정의 운동만) ----------

@router.put(
    "/workout-exercise/{exercise_id}",
    description="운동 하나의 이수 여부를 바꾼다. 본문 {\"completed\": true}. 본인 일정의 운동이 아니면 404.",
)
def update_workout_exercise(exercise_id: int, body: WorkoutExerciseUpdate, user_id: int = Depends(current_user_id)):
    exercise = set_workout_exercise_completed(user_id, exercise_id, body.completed)
    if exercise is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="운동을 찾을 수 없습니다.")
    return exercise



# ---------- 매주 반복 일정 (수업 시간표, 요일별 운동 불가 시간) ----------

BLOCK_NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="반복 일정을 찾을 수 없습니다.")


def block_conflict(e: BlockConflictError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"message": "같은 요일의 다른 반복 일정과 시간이 겹칩니다.", "conflicts": e.conflicts},
    )


@router.get("/weekly-block", description="본인의 매주 반복 일정 목록 (요일, 시작 시각 순).")
def read_weekly_blocks(user_id: int = Depends(current_user_id)):
    return list_weekly_blocks(user_id)


@router.get("/weekly-block/{block_id}")
def read_weekly_block(block_id: int, user_id: int = Depends(current_user_id)):
    block = get_weekly_block(user_id, block_id)
    if block is None:
        raise BLOCK_NOT_FOUND
    return block


@router.post(
    "/weekly-block",
    status_code=status.HTTP_201_CREATED,
    description="매주 반복 일정 추가 (예: 매주 월 09:00~10:15 자료구조). 같은 요일의 다른 반복 일정과 겹치면 409.",
)
def create_weekly_block(body: WeeklyBlockIn, user_id: int = Depends(current_user_id)):
    try:
        return insert_weekly_block(user_id, body.weekday, body.start_time, body.end_time, body.name)
    except BlockConflictError as e:
        raise block_conflict(e)


@router.put("/weekly-block/{block_id}", description="반복 일정 전체를 바꾼다. 겹치면 409.")
def replace_weekly_block(block_id: int, body: WeeklyBlockIn, user_id: int = Depends(current_user_id)):
    try:
        block = update_weekly_block(user_id, block_id, body.weekday, body.start_time, body.end_time, body.name)
    except BlockConflictError as e:
        raise block_conflict(e)
    if block is None:
        raise BLOCK_NOT_FOUND
    return block


@router.delete("/weekly-block/{block_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_weekly_block(block_id: int, user_id: int = Depends(current_user_id)):
    if not delete_weekly_block(user_id, block_id):
        raise BLOCK_NOT_FOUND
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- 주간 리포트 ----------

@router.get(
    "/report",
    description=(
        "주간 운동 리포트 (월~일). AI가 계획한 운동의 종목별 이수 여부로 요일별 수행률(%)을 계산한다.\n"
        "total은 주 전체(앞으로 할 운동 포함), so_far는 오늘까지. 계획이 없는 날의 rate는 null."
    ),
)
def read_report(
    user_id: int = Depends(current_user_id),
    week: date | None = Query(None, description="이 날짜가 속한 주 (YYYY-MM-DD). 없으면 이번 주"),
):
    return weekly_report(user_id, week or today())
