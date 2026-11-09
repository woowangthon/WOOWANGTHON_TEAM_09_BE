# HTTP 엔드포인트. 요청을 받아 planner·auth·db에 넘기고, 오류를 HTTP 응답으로 바꾸기만 한다.

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.auth import create_access_token, current_user_id, hash_password, verify_password
from app.db import (
    IdentityColumnError, NameTakenError, ScheduleConflictError,
    delete_schedule, fetch_login, fetch_user, fetch_user_info, get_schedule, insert_schedule, insert_user,
    list_schedules, update_schedule, upsert_user_info,
)
from app.planner import PlanError, create_plan
from app.schemas import InfoCreate, LoginRequest, PlanRequest, ScheduleIn, UserCreate

router = APIRouter()


@router.get("/")
def read_root():

    # response = client.chat.completions.create(
    #     model="claude-sonnet-5",
    #     messages=[{"role": "user", "content": "Hello!"}],
    # )
    # print(response.choices[0].message.content)

    return {"Hello": "World"}


@router.get(
    "/output",
    description="사용자 정보와 기간을 받아 운동 계획을 만든다.",
)
def output(req: Annotated[PlanRequest, Query()]):
    try:
        return create_plan(req)
    except PlanError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post(
    "/user",
    status_code=status.HTTP_201_CREATED,
    description="회원가입. 비밀번호는 Argon2id(무작위 salt 포함) 해시로만 저장한다. 내부 id는 응답에 넣지 않는다.",
)
def create_user(body: UserCreate):
    try:
        return insert_user(body.name, hash_password(body.password))
    except NameTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 아이디입니다.")


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
        "로그인한 본인의 건강 정보(나이, 키, 몸무게)를 저장한다. 없으면 만들고, 있으면 덮어쓴다(upsert).\n"
        "헤더: Authorization: Bearer <토큰>."
    ),
)
def save_info(body: InfoCreate, user_id: int = Depends(current_user_id)):
    try:
        return upsert_user_info(user_id, body.age, body.height, body.weight)
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


@router.get("/schedule/{schedule_id}")
def read_schedule(schedule_id: int, user_id: int = Depends(current_user_id)):
    schedule = get_schedule(user_id, schedule_id)
    if schedule is None:
        raise SCHEDULE_NOT_FOUND
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
