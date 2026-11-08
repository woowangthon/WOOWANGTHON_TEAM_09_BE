"""경로, 환경변수, AI 모델 설정. 설정값은 모두 여기서 바꾼다."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ---------- 경로 ----------
BASE_DIR = Path(__file__).resolve().parent.parent          # server/
DOCUMENTS_DIR = BASE_DIR / "preprocess" / "result"          # 전처리된 참고 문서(*.txt)
EXERCISES_CSV = BASE_DIR / "exercises.csv"                  # 계획에 쓸 수 있는 운동 목록
LLM_CACHE_DIR = BASE_DIR / ".llm_cache"                     # AI 응답 캐시

# ---------- AI ----------
NOVA_AI_KEY = os.getenv("NOVA_AI_KEY")
AI_BASE_URL = "https://factchat-cloud.mindlogic.ai/v1/gateway"
AI_MODEL = "claude-sonnet-5"

# 같은 요청은 저장해 둔 응답을 돌려줘 토큰을 쓰지 않는다. LLM_CACHE=0 이면 끈다.
LLM_CACHE_ENABLED = os.getenv("LLM_CACHE", "1") != "0"
