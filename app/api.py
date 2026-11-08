"""HTTP 엔드포인트. 요청을 받아 planner에 넘기고, 오류를 HTTP 응답으로 바꾸기만 한다."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.planner import PlanError, create_plan
from app.schemas import PlanRequest

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
