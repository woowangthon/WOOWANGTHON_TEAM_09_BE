"""HTTP 엔드포인트. 요청을 받아 planner·auth·db에 넘기고, 오류를 HTTP 응답으로 바꾸기만 한다."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import create_access_token, current_user_id, hash_password, verify_password
from app.db import (
    IdentityColumnError, NameTakenError,
    fetch_login, fetch_user, fetch_user_info, insert_user, upsert_user_info,
)
from app.planner import PlanError, create_plan
from app.schemas import InfoCreate, LoginRequest, PlanRequest, UserCreate

router = APIRouter()


@router.get("/")
def read_root():

    # response = client.chat.completions.create(
    #     model="claude-sonnet-5",
    #     messages=[{"role": "user", "content": "Hello!"}],
    # )
    # print(response.choices[0].message.content)

    return {"Hello": "World"}


@router.get("/output")
def output(req: Annotated[PlanRequest, Query()]):
    """사용자 정보와 기간을 받아 운동 계획을 만든다."""
    try:
        return create_plan(req)
    except PlanError as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)


@router.post("/user", status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate):
    """회원가입. 비밀번호는 Argon2id(무작위 salt 포함) 해시로만 저장한다. 내부 id는 응답에 넣지 않는다."""
    try:
        return insert_user(body.name, hash_password(body.password))
    except NameTakenError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="이미 사용 중인 아이디입니다.")


@router.post("/login")
def login(body: LoginRequest):
    """아이디·비밀번호가 맞으면 JWT 액세스 토큰을 준다. 아이디와 비밀번호 중 무엇이 틀렸는지는 알려주지 않는다."""
    user = fetch_login(body.name)
    if not verify_password(user["password"] if user else None, body.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="아이디 또는 비밀번호가 올바르지 않습니다.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return {"access_token": create_access_token(user["id"]), "token_type": "bearer"}


@router.get("/user")
def read_me(user_id: int = Depends(current_user_id)):
    """로그인한 본인의 정보. 헤더: Authorization: Bearer <토큰>. 건강 정보(info)가 없으면 info는 null."""
    user = fetch_user(user_id)
    if user is None:   # 토큰은 유효하지만 탈퇴 등으로 사용자가 없는 경우
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="사용자를 찾을 수 없습니다.")
    return {**user, "info": fetch_user_info(user_id)}


@router.post("/info")
def save_info(body: InfoCreate, user_id: int = Depends(current_user_id)):
    """로그인한 본인의 건강 정보(나이, 키, 몸무게)를 저장한다. 없으면 만들고, 있으면 덮어쓴다(upsert).
    헤더: Authorization: Bearer <토큰>."""
    try:
        return upsert_user_info(user_id, body.age, body.height, body.weight)
    except IdentityColumnError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="DB 설정 오류: info.id가 자동 증가(identity) 열이라 사용자 id를 넣을 수 없습니다.",
        )
