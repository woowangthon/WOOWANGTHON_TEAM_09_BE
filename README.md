# 대학생 맞춤형 헬스 AI 서버

사용자 정보(나이, 키·몸무게, 목표, 운동 경력)와 기간을 받아, 운동 지침 문서를 근거로(RAG) 날짜별 헬스 계획을 만들어 주는 FastAPI 서버입니다.

## 실행

```bash
source myenv/bin/activate
uvicorn main:app --reload
```

- `.env`에 `NOVA_AI_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `JWT_SECRET`이 있어야 합니다.
- 예시 요청 (가입 `POST /user` → 로그인 `POST /login` → 건강 정보 `POST /info` 후):
  `POST /plan` 헤더 `Authorization: Bearer <토큰>`, 본문 `{"goal": "다이어트", "week": "2026-11-16"}` (그 주 월~일)
- API 문서: 서버 실행 후 `/docs` (스웨거), `/openapi.json` (FE·AI 도구에 그대로 넣을 수 있는 명세)

## 폴더 구조

```
main.py                 진입점. FastAPI 앱을 만들고 app/api.py의 라우터를 붙인다
app/
  api.py                엔드포인트 (/, /plan, /user, /login, /info, /schedule, /weekly-block, /workout-exercise, /report). 오류를 HTTP 응답으로 바꾼다
  auth.py               비밀번호 해시(Argon2id), JWT 발급·검증
  db.py                 Supabase 읽기·쓰기 (user, info, schedule)
  planner.py            ★ 계획 생성 전체 흐름. 처음 읽을 때 여기서 시작
  profile.py            BMI·대상(성인/노인 등) 계산, 건강 상태로 추가 대상 찾기
  exercises.py          운동 목록(exercises.csv) 읽기, 고강도 운동 목록
  departments.py        충북대 학과 -> 단과대학 (departments.csv)
  schemas.py            요청 형식(PlanRequest 등)과 AI 출력(WorkoutPlan) 형식
  responses.py          API 응답 형식 (스웨거 /docs의 응답 스키마)
  examples.py           스웨거 요청 예시 (한 사용자 상황으로 맞춤)
  prompts.py            시스템 프롬프트, AI에게 보낼 메시지 조립
  search.py             검색어 만들기, 참고 문서 조각 고르기
  report.py             주간 리포트(요일별 수행률), 프롬프트용 지난주 수행 요약, 단과대학 대시보드
  ocr.py                이미지 -> 글자 줄 (RapidOCR, 한국어 모델, 기울기 보정)
  inbody.py             OCR 결과에서 인바디 키·몸무게·체지방률·골격근량·측정일 찾기 (POST /inbody/image)
  week.py               주(월~일) 계산, 한국 시간 기준 오늘
  schedule.py           캠퍼스 일정(지금은 모두 공강, 나중에 Supabase 연동), 운동 종료 시각 계산
  llm.py                AI 호출 (캐시, 토큰 사용량 로그)
  logs.py               구조화된 로그 (JSON 한 줄), 접속·응답 로그 미들웨어
  config.py             경로, 환경변수, 모델 이름
  rag/
    documents.py        문서별 섹션 규칙 (섹션마다 대상을 붙임)
    chunks.py           섹션을 조각으로 나누기
    retriever.py        BM25 검색
preprocess/
  main.py               src/의 PDF -> original-result/의 텍스트 추출
  clean.py              original-result/ -> result/ 정제 (성인·공통만, 잡음 줄 삭제. 줄 삭제만 함)
  src/                  원본 PDF
  original-result/      추출한 원문 텍스트 (그대로 보관)
  result/               정제된 텍스트 (검색 대상)
exercises.csv           계획에 쓸 수 있는 운동 (부위, 근육, 운동 이름)
departments.csv         충북대 학과 -> 단과대학 (홈페이지 단과대학 안내 기준)
```

## 요청 하나가 처리되는 순서 (`app/planner.py`)

1. **기간 정하기**: 요청한 날짜(`week`, 없으면 오늘)가 속한 주의 월~일. 이미 지난 요일은 뺀다 (`app/week.py`)
2. **프로필 계산**: DB의 `info`(나이·키·몸무게·경력)로 대상(성인 등)과 BMI·비만 단계를 서버가 계산. 목표와 기간은 요청 본문에서 받음
3. **고를 수 있는 운동**: `exercises.csv`의 운동. 비만(BMI 25 이상)이면 고강도 운동은 뺀다
4. **참고 문서 고르기**:
   - 대상의 핵심 지침(신체활동 지침서의 권장량)은 항상 넣는다
   - 나머지는 대상이 맞거나 "공통"인 조각 중에서 한국어·영어 검색어로 검색
5. **프롬프트 조립**: `[참고 문서]`, `[운동 목록]`, `[계획 기간]`, `[캠퍼스 일정]`(DB의 schedule + 매주 반복 weekly_block), `[지난주 운동 수행]`(요일별·시간대별 수행률), `[사용자 정보]`
6. **AI 호출**: 구조화된 출력(JSON Schema)으로 계획을 받는다. 운동 이름은 스키마로 목록 안에서만 고르게 한다
7. **검증**: 형식을 확인하고, 각 운동에 부위·근육 정보를 CSV에서 붙인다
8. **일정 보정·저장**: 운동 시간이 일정과 겹치거나 운동 가능 시간을 벗어나면 그날 가장 가까운 빈 시간으로 옮기고(빈 시간이 없으면 휴식), 운동하는 날을 `schedule`에 `운동: <초점>` 이름으로 저장

## 자주 하는 작업

| 하고 싶은 일 | 고칠 곳 |
|---|---|
| AI 규칙(말투, 계획 원칙) 바꾸기 | `app/prompts.py`의 `SYSTEM_PROMPT` |
| 운동 추가·수정 | `exercises.csv` |
| 학과·단과대학 추가·수정 | `departments.csv` |
| 고강도로 볼 운동 바꾸기 | `app/exercises.py`의 `HIGH_INTENSITY_EXERCISES` |
| 참고 문서 추가 | PDF를 `preprocess/src/`에 넣고 `python preprocess/main.py` 실행 → `app/rag/documents.py`의 `SPLITTERS`에 섹션 규칙 추가 (없으면 문서 전체가 "공통") → 필요하면 `preprocess/clean.py`의 `DOCS`에 삭제 규칙 추가 → `python preprocess/clean.py` 실행 |
| 검색어 사전 바꾸기 | `app/search.py`의 `GOAL_QUERIES`, `TARGET_EN` |
| 응답·AI 출력 필드 바꾸기 | `app/schemas.py` |
| 모델·API 주소 바꾸기 | `app/config.py` |

문서와 운동 목록은 서버 시작 시 한 번 읽으므로, 바꾼 뒤에는 서버를 재시작해야 합니다.

## AI 응답 캐시

- 같은 요청(프롬프트·스키마가 완전히 같음)은 `.llm_cache/`에 저장된 응답을 돌려주어 토큰을 쓰지 않습니다.
- 끄려면 `LLM_CACHE=0 uvicorn main:app`
- 실제로 호출하면 토큰 사용량이 로그(`ai.call`)에 남습니다: `prompt_tokens`, `completion_tokens`, `total_tokens`

## 인바디 사진 읽기 (OCR)

- `POST /inbody/image`: 결과지 사진에서 키·몸무게·체지방률·골격근량·측정일을 읽어 돌려준다 (저장은 FE가 확인 후 `POST /info`). 회원가입 화면에서도 쓰도록 로그인 없이 호출 가능
- 오픈소스 [RapidOCR](https://github.com/RapidAI/RapidOCR) (Apache-2.0)을 ONNX Runtime으로 서버에서 실행한다. 외부 API 호출·비용 없음
  - 글자 위치: PP-OCRv6 det (패키지에 포함) / 글자 인식: 한국어 PP-OCRv5 rec mobile (처음 쓸 때 내려받음, 약 13MB)
- **배포할 때 모델을 미리 받기**: 빌드 단계에서 `python -m app.ocr` 실행 (안 하면 첫 요청이 모델을 내려받느라 느리거나 실패할 수 있음)
- 한 장에 약 5초 (기울어진 사진은 반듯하게 돌려 한 번 더 읽어 약 10초), 메모리 최대 약 330MB. 동시에 한 장씩만 처리한다

## 로그 (구조화된 로그)

모든 로그는 한 줄에 JSON 하나로 표준 출력에 나옵니다 (`app/logs.py`). 수준은 환경변수 `LOG_LEVEL` (기본 `INFO`, `DEBUG`면 OCR이 읽은 글자 전체도).

- 공통 키: `ts`(한국 시간), `level`, `logger`, `event`, `request_id`(요청 중 로그를 묶음)
- 비밀번호·토큰은 남기지 않습니다 (요청 본문은 남기지 않고, 응답의 `access_token`은 `***`)

| event | 언제 | 주요 키 |
|---|---|---|
| `http.request` | 요청이 들어옴 | `method`, `path`, `query`, `client`(IP), `user_agent`, `content_type`, `content_length` |
| `http.response` | 응답을 보냄 | `status`, `duration_ms`, `user_id`(로그인한 요청), `response`(응답 요약: 값은 그대로, 목록은 개수와 첫 항목) |
| `http.error` | 처리 중 잡히지 않은 예외 | `error`(traceback) |
| `ocr.read` | 사진 OCR | `image_bytes`, `image_size`, `lines`(읽은 줄 수), `seconds`, `skew_degrees`, `rotated_and_reread` |
| `ocr.extract` | 결과지에서 값 찾기 | `result`(키·몸무게·체지방률·골격근량·측정일), `missing`, `warnings`, `candidates`(항목별 후보 값과 출처) |
| `ocr.lines` (DEBUG) | OCR이 읽은 글자 전체 | `lines` (`글자 @(x,y) 신뢰도`) |
| `ocr.engine_error` | OCR 엔진을 못 씀 | `error` |
| `ai.call` / `ai.cache_hit` | AI 호출 / 캐시 사용 | `model`, `seconds`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `finish_reason` |
| `log` | 그 밖의 라이브러리 로그 (uvicorn 등) | `message` |

보기 좋게 보기: `uvicorn main:app 2>&1 | jq .` / 요청 하나만: `jq 'select(.request_id=="df010971")'`

## 참고 문서 (RAG 자료 출처)

운동 계획의 근거로 쓰는 문서입니다 (원본 PDF: `preprocess/src/`, 전처리 결과: `preprocess/result/`). 교내 대회용 비영리 목적으로만 사용합니다.

| 문서명 | 내용 | 출처 | 라이선스 |
|---|---|---|---|
| WHO 신체활동 및 좌식행동 가이드라인 | 연령대별 권장 신체활동량(유산소·근력), 좌식행동 줄이기 권고 | 세계보건기구(WHO), 2020 (한국어판) | CC BY-NC-SA 3.0 IGO |
| 신체활동 지침서 (한국인을 위한 신체활동 지침서 개정판) | 한국인 생애주기별(아동·청소년·성인·노인 등) 신체활동 권장 기준 | 보건복지부·한국건강증진개발원, 2023 | 명시 없음 (공공기관 발행물) |
| 한국인을 위한 달리기 가이드라인 | 달리기의 효과, 올바른 자세, 강도·속도별 달리기 방법 | 보건복지부·한국건강증진개발원, 2022 | 명시 없음 (공공기관 발행물) |
| 성인을 위한 신체활동 가이드라인 및 운동 강도에 대한 고찰 | 해외 성인 신체활동 지침 소개, 신체활동·운동 강도 분류 제안 | 박동호·김창선·김광준, 운동과학 24(2): 99-107, 2015 (doi:10.15857/ksep.2015.24.2.99) | CC BY-NC 4.0 |
| 2022 Clinical Practice Guidelines for Obesity Management in Korea (`jomes-32-1-1`) | 한국 비만 진료지침: 진단 기준, 운동·식사·약물 치료 권고 | 대한비만학회, J Obes Metab Syndr 32: 1-24, 2023 | CC BY-NC 4.0 |
| 비만 | 비만의 정의(BMI 25 이상), 동반 질환, 치료 목표와 방법 | 질병관리청 국가건강정보포털 ([링크](https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=6694)) | 공공누리 제4유형 (출처표시·상업용금지·변경금지) |
| 운동 | 신체활동과 운동의 구분, 운동 강도·종류, 고강도 간헐적 운동(HIIT) 주의점 | 질병관리청 국가건강정보포털 ([링크](https://health.kdca.go.kr/healthinfo/biz/health/gnrlzHealthInfo/gnrlzHealthInfo/gnrlzHealthInfoView.do?cntnts_sn=5293)) | 공공누리 제4유형 (출처표시·상업용금지·변경금지) |
| American College of Sports Medicine Position Stand: Resistance Training Prescription | 건강한 성인의 근력운동 처방(강도·세트·빈도 등)이 근기능·근비대에 미치는 영향 | ACSM (Currier 외), Med Sci Sports Exerc 58(4): 851-872, 2026 | CC BY-NC-ND 4.0 |

- 비영리 라이선스(CC BY-NC 계열, 공공누리 제4유형) 문서가 포함되어 있어 상업적으로 이용할 수 없습니다.
- WHO 가이드라인 한국어판: 본 번역물은 세계보건기구(WHO)에서 제작한 것이 아니며, WHO는 본 번역물의 내용이나 정확성에 대해 책임지지 않습니다. 영문판 원본이 진본이며 구속력을 가집니다.
- 원문 PDF는 출처 확인용으로 둡니다. 전처리 결과는 검색을 위해 원문에서 필요 없는 부분(머리글·쪽번호, 그림 속 글자, 참고문헌, 약물·수술 치료처럼 운동과 무관한 부분 등)을 뺀 것이며, 남은 문장은 고쳐 쓰지 않았습니다.

## 사용한 오픈소스 소프트웨어

버전은 `requirements.txt`을 참고해주세요.

| 이름 | 용도 | 라이선스 | 출처 |
|---|---|---|---|
| RapidOCR 3.10.0 | 인바디 결과지 사진의 글자 읽기 (OCR) | Apache-2.0 | [github.com/RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR) |
| PaddleOCR 모델 (PP-OCRv6 글자 위치, 한국어 PP-OCRv5 글자 인식) | RapidOCR이 쓰는 OCR 모델 | Apache-2.0 | [github.com/PaddlePaddle/PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) |
| ONNX Runtime 1.31.1 | OCR 모델 실행 (CPU) | MIT | [onnxruntime.ai](https://onnxruntime.ai) |
| FastAPI 0.162.0 | API 서버, 스웨거 문서 | MIT | [github.com/fastapi/fastapi](https://github.com/fastapi/fastapi) |
| supabase-py 2.34.1 | Supabase(DB) 접근 | MIT | [github.com/supabase/supabase-py](https://github.com/supabase/supabase-py) |
| OpenAI Python SDK 3.46.0 | AI 게이트웨이 호출 (OpenAI 호환 API) | Apache-2.0 | [github.com/openai/openai-python](https://github.com/openai/openai-python) |
