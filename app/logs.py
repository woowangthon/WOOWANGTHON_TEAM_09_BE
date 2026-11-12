# 구조화된 로그 (한 줄에 JSON 하나). 개발자가 요청 하나의 흐름을 따라가며 볼 수 있게 한다.
#
# 모든 줄의 공통 키: ts(한국 시간), level, logger, event(무슨 일인지, 점으로 구분한 이름), request_id(요청 중이면)
# 나머지 키는 event마다 다르다. 로그 남기는 법:  log_event(logger, "ocr.read", lines=292, seconds=4.4)
#
# 주요 event
#   http.request   요청이 들어옴: method, path, query, client(IP), user_agent, content_type, content_length
#   http.response  응답을 보냄: status, duration_ms, user_id(로그인한 요청), response(응답 요약: 주요 값)
#   http.error     처리 중 잡히지 않은 예외 (traceback 포함) -> 500
#   ocr.read / ocr.extract / ocr.engine_error   인바디 OCR (app/ocr.py, app/inbody.py, app/api.py)
#   ai.call / ai.cache_hit                      AI 호출 토큰 사용량 (app/llm.py)
#
# 비밀번호·토큰은 남기지 않는다 (요청 본문은 남기지 않고, 응답 요약에서 access_token 등은 가린다).
# 보기 좋게 보려면:  uvicorn main:app 2>&1 | jq .   (특정 요청만: jq 'select(.request_id=="ab12cd34")')

import json
import logging
import sys
import time
import traceback
import uuid
from contextvars import ContextVar
from datetime import datetime

from app.config import TIMEZONE

# 요청 하나의 정보 {"request_id", "user_id"}. dict를 공유하므로 스레드(동기 엔드포인트)에서 user_id를 넣어도 보인다.
_request: ContextVar[dict | None] = ContextVar("request", default=None)

SECRET_KEYS = {"access_token", "password", "token", "authorization"}
MAX_SUMMARY_BYTES = 64 * 1024   # 이보다 큰 응답(예: /openapi.json)은 내용 요약 없이 크기만 남긴다
MAX_TEXT = 80                   # 응답 요약에서 문자열을 이 길이로 자른다


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, TIMEZONE).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or "log",   # log_event가 아닌 일반 로그(라이브러리 등)는 "log"
        }
        if (ctx := _request.get()) is not None:
            entry["request_id"] = ctx["request_id"]
        entry.update(getattr(record, "fields", {}))
        if getattr(record, "event", None) is None:
            entry["message"] = record.getMessage()
        if record.exc_info:
            entry["error"] = "".join(traceback.format_exception(*record.exc_info)).strip()
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging(level: int | str = logging.INFO) -> None:
    # 루트와 uvicorn 로거를 JSON 한 줄 형식으로 바꾼다. uvicorn 기본 접속 로그는 끄고 AccessLogMiddleware가 대신 남긴다.
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True
    for noisy in ("httpx", "httpx2", "httpcore", "hpack"):   # Supabase·AI 호출마다 찍히는 HTTP 라이브러리 로그
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, exc_info=None, **fields) -> None:
    logger.log(level, event, extra={"event": event, "fields": fields}, exc_info=exc_info)


def set_user_id(user_id: int) -> None:
    # 로그인 확인(app/auth.py)이 부른다 -> http.response에 user_id가 남는다
    if (ctx := _request.get()) is not None:
        ctx["user_id"] = user_id


def summarize(value, depth: int = 0):
    # 응답 JSON을 로그용으로 줄인다: 값(숫자·문자열·null)은 그대로, 목록은 개수(+첫 항목 요약), 깊은 객체는 키만.
    if isinstance(value, dict):
        if depth >= 2:
            return f"{{{len(value)} keys}}"
        return {k: ("***" if k.lower() in SECRET_KEYS else summarize(v, depth + 1)) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        if all(not isinstance(v, (dict, list)) for v in value) and len(value) <= 10:
            return [summarize(v, depth + 1) for v in value]
        return {"count": len(value), "first": summarize(value[0], depth)}   # 첫 항목은 같은 깊이로 (예: 대시보드 1위)
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + "…"
    return value


access_logger = logging.getLogger("app.access")


class AccessLogMiddleware:
    # 모든 HTTP 요청의 접속(http.request)과 응답(http.response) 로그. 응답 본문은 JSON이면 요약해 남긴다.
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        ctx = {"request_id": uuid.uuid4().hex[:8], "user_id": None}
        token = _request.set(ctx)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        log_event(access_logger, "http.request",
                  method=scope["method"], path=scope["path"], query=scope.get("query_string", b"").decode() or None,
                  client=(scope.get("client") or [None])[0], user_agent=headers.get("user-agent"),
                  content_type=headers.get("content-type"), content_length=headers.get("content-length"))
        started = time.perf_counter()
        status, content_type, body, size = 500, "", bytearray(), 0

        async def send_and_capture(message):
            nonlocal status, content_type, size
            if message["type"] == "http.response.start":
                status = message["status"]
                content_type = dict(message.get("headers") or []).get(b"content-type", b"").decode("latin-1")
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                size += len(chunk)
                if "json" in content_type and len(body) + len(chunk) <= MAX_SUMMARY_BYTES:
                    body.extend(chunk)
            await send(message)

        try:
            await self.app(scope, receive, send_and_capture)
        except Exception:
            log_event(access_logger, "http.error", logging.ERROR, exc_info=True,
                      method=scope["method"], path=scope["path"], user_id=ctx["user_id"])
            raise
        finally:
            response = None
            if body and size <= MAX_SUMMARY_BYTES:
                try:
                    response = summarize(json.loads(body))
                except ValueError:
                    pass
            log_event(access_logger, "http.response", logging.WARNING if status >= 500 else logging.INFO,
                      method=scope["method"], path=scope["path"], status=status,
                      duration_ms=round((time.perf_counter() - started) * 1000, 1),
                      user_id=ctx["user_id"], response_bytes=size, response=response)
            _request.reset(token)
