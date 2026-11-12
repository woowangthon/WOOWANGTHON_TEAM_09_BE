# AI 호출. 같은 요청은 캐시에서 돌려주고, 실제 호출하면 토큰 사용량을 로그로 남긴다.

import hashlib
import json
import logging
import time

from openai import OpenAI

from app.config import AI_BASE_URL, LLM_CACHE_DIR, LLM_CACHE_ENABLED, NOVA_AI_KEY
from app.logs import log_event

logger = logging.getLogger(__name__)

client = OpenAI(api_key=NOVA_AI_KEY, base_url=AI_BASE_URL)


def chat_completion(**kwargs) -> str:
    # client.chat.completions.create 결과의 본문 문자열. 캐시가 있으면 API를 부르지 않는다.
    #
    # 캐시 키는 요청 전체(모델, 메시지, 응답 형식)이므로 프롬프트가 한 글자라도 바뀌면 새로 호출한다.
    key = hashlib.sha256(json.dumps(kwargs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    cache_file = LLM_CACHE_DIR / f"{key}.json"
    if LLM_CACHE_ENABLED and cache_file.exists():
        log_event(logger, "ai.cache_hit", model=kwargs.get("model"), cache_key=key[:12])
        return json.loads(cache_file.read_text(encoding="utf-8"))["content"]

    started = time.perf_counter()
    response = client.chat.completions.create(**kwargs)
    content = response.choices[0].message.content
    usage = response.usage
    log_event(logger, "ai.call", model=kwargs.get("model"), seconds=round(time.perf_counter() - started, 2),
              prompt_tokens=usage.prompt_tokens if usage else None,
              completion_tokens=usage.completion_tokens if usage else None,
              total_tokens=usage.total_tokens if usage else None,
              finish_reason=response.choices[0].finish_reason, response_chars=len(content or ""))
    if LLM_CACHE_ENABLED:
        LLM_CACHE_DIR.mkdir(exist_ok=True)
        cache_file.write_text(json.dumps({"content": content}, ensure_ascii=False), encoding="utf-8")
    return content
