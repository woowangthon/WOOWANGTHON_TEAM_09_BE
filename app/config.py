# 경로, 환경변수, AI 모델 설정. 설정값은 모두 여기서 바꾼다.

import os
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

# ---------- 경로 ----------
BASE_DIR = Path(__file__).resolve().parent.parent          # server/
DOCUMENTS_DIR = BASE_DIR / "preprocess" / "result"          # 전처리된 참고 문서(*.txt)
EXERCISES_CSV = BASE_DIR / "exercises.csv"                  # 계획에 쓸 수 있는 운동 목록
DEPARTMENTS_CSV = BASE_DIR / "departments.csv"              # 충북대 학과 -> 단과대학 (GET /dashboard 단과대 경쟁)
LLM_CACHE_DIR = BASE_DIR / ".llm_cache"                     # AI 응답 캐시

# ---------- 계획 ----------
TOP_K = 4            # AI에게 줄 참고 문서 조각 수
WORKOUT_HOURS = ("08:00", "22:00")   # 운동할 수 있는 시간대 (헬스장 운영 시간 등에 맞게 바꾼다)

# ---------- AI ----------
NOVA_AI_KEY = os.getenv("NOVA_AI_KEY")
AI_BASE_URL = "https://factchat-cloud.mindlogic.ai/v1/gateway"
AI_MODEL = "claude-sonnet-5"

# 같은 요청은 저장해 둔 응답을 돌려줘 토큰을 쓰지 않는다. LLM_CACHE=0 이면 끈다.
LLM_CACHE_ENABLED = os.getenv("LLM_CACHE", "1") != "0"

# ---------- Supabase ----------
# 서버 전용 키(service_role)는 RLS를 무시하므로 .env에만 두고 앱(클라이언트)에 절대 넣지 않는다.
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TIMEZONE = ZoneInfo("Asia/Seoul")   # DB의 timestamptz(UTC 저장)를 이 시간대로 바꿔 쓴다

# ---------- 로그인 (JWT) ----------
JWT_SECRET = os.getenv("JWT_SECRET")              # 길고 무작위한 값. 예: python -c "import secrets; print(secrets.token_urlsafe(64))"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))   # 토큰 유효 기간(분). 환경변수로 정한다 (없으면 60분)

# ---------- 로그 ----------
# 구조화된 로그(JSON 한 줄) 수준. DEBUG면 OCR이 읽은 글자 전체(ocr.lines)도 남긴다
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ---------- CORS (브라우저에서 이 API를 부를 수 있는 프론트엔드 주소) ----------
# 쉼표로 여러 개. 배포한 FE 주소는 환경변수 CORS_ORIGINS로 넣는다 (예: http://localhost:5173,https://app.example.com)
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
