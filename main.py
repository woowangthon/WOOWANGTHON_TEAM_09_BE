# 서버 진입점. 실행: uvicorn main:app --reload
#
# 코드는 app/ 패키지에 있다. 구조와 요청 처리 흐름은 README.md 참고.

import logging

from fastapi import FastAPI

from app.api import router

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")

app = FastAPI()
app.include_router(router)
