# 대학생 맞춤형 헬스 AI 서버

사용자 정보(나이, 키·몸무게, 목표, 운동 경력)와 기간을 받아, 운동 지침 문서를 근거로(RAG) 날짜별 헬스 계획을 만들어 주는 FastAPI 서버입니다.

## 실행

```bash
source myenv/bin/activate
uvicorn main:app --reload
```

- `.env`에 `NOVA_AI_KEY`, `SUPABASE_URL`, `SUPABASE_KEY`, `JWT_SECRET`이 있어야 합니다.
- 예시 요청 (가입 `POST /user` → 로그인 `POST /login` → 건강 정보 `POST /info` 후):
  `POST /plan` 헤더 `Authorization: Bearer <토큰>`, 본문 `{"goal": "다이어트", "start_date": "2026-11-09", "end_date": "2026-11-15"}`
- API 문서: 서버 실행 후 `/docs`

## 폴더 구조

```
main.py                 진입점. FastAPI 앱을 만들고 app/api.py의 라우터를 붙인다
app/
  api.py                엔드포인트 (/, /plan, /user, /login, /info, /schedule). 오류를 HTTP 응답으로 바꾼다
  auth.py               비밀번호 해시(Argon2id), JWT 발급·검증
  db.py                 Supabase 읽기·쓰기 (user, info, schedule)
  planner.py            ★ 계획 생성 전체 흐름. 처음 읽을 때 여기서 시작
  profile.py            BMI·대상(성인/노인 등) 계산, 건강 상태로 추가 대상 찾기
  exercises.py          운동 목록(exercises.csv) 읽기, 고강도 운동 목록
  schemas.py            요청(PlanRequest)과 AI 출력(WorkoutPlan) 형식
  prompts.py            시스템 프롬프트, AI에게 보낼 메시지 조립
  search.py             검색어 만들기, 참고 문서 조각 고르기
  schedule.py           캠퍼스 일정(지금은 모두 공강, 나중에 Supabase 연동), 운동 종료 시각 계산
  llm.py                AI 호출 (캐시, 토큰 사용량 로그)
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
```

## 요청 하나가 처리되는 순서 (`app/planner.py`)

1. **기간 확인**: 시작일이 없으면 오늘, 종료일이 없으면 7일. 최대 14일 (`app/config.py`의 `MAX_PLAN_DAYS`)
2. **프로필 계산**: DB의 `info`(나이·키·몸무게·경력)로 대상(성인 등)과 BMI·비만 단계를 서버가 계산. 목표와 기간은 요청 본문에서 받음
3. **고를 수 있는 운동**: `exercises.csv`의 운동. 비만(BMI 25 이상)이면 고강도 운동은 뺀다
4. **참고 문서 고르기**:
   - 대상의 핵심 지침(신체활동 지침서의 권장량)은 항상 넣는다
   - 나머지는 대상이 맞거나 "공통"인 조각 중에서 한국어·영어 검색어로 검색
5. **프롬프트 조립**: `[참고 문서]`, `[운동 목록]`, `[계획 기간]`, `[캠퍼스 일정]`(DB의 schedule), `[사용자 정보]`
6. **AI 호출**: 구조화된 출력(JSON Schema)으로 계획을 받는다. 운동 이름은 스키마로 목록 안에서만 고르게 한다
7. **검증**: 형식을 확인하고, 각 운동에 부위·근육 정보를 CSV에서 붙인다
8. **일정 보정·저장**: 운동 시간이 일정과 겹치거나 운동 가능 시간을 벗어나면 그날 가장 가까운 빈 시간으로 옮기고(빈 시간이 없으면 휴식), 운동하는 날을 `schedule`에 `운동: <초점>` 이름으로 저장

## 자주 하는 작업

| 하고 싶은 일 | 고칠 곳 |
|---|---|
| AI 규칙(말투, 계획 원칙) 바꾸기 | `app/prompts.py`의 `SYSTEM_PROMPT` |
| 운동 추가·수정 | `exercises.csv` |
| 고강도로 볼 운동 바꾸기 | `app/exercises.py`의 `HIGH_INTENSITY_EXERCISES` |
| 참고 문서 추가 | PDF를 `preprocess/src/`에 넣고 `python preprocess/main.py` 실행 → `app/rag/documents.py`의 `SPLITTERS`에 섹션 규칙 추가 (없으면 문서 전체가 "공통") → 필요하면 `preprocess/clean.py`의 `DOCS`에 삭제 규칙 추가 → `python preprocess/clean.py` 실행 |
| 검색어 사전 바꾸기 | `app/search.py`의 `GOAL_QUERIES`, `TARGET_EN` |
| 응답·AI 출력 필드 바꾸기 | `app/schemas.py` |
| 모델·API 주소 바꾸기 | `app/config.py` |

문서와 운동 목록은 서버 시작 시 한 번 읽으므로, 바꾼 뒤에는 서버를 재시작해야 합니다.

## AI 응답 캐시

- 같은 요청(프롬프트·스키마가 완전히 같음)은 `.llm_cache/`에 저장된 응답을 돌려주어 토큰을 쓰지 않습니다.
- 끄려면 `LLM_CACHE=0 uvicorn main:app`
- 실제로 호출하면 토큰 사용량이 로그에 남습니다: `AI 토큰 사용량: 입력 … / 출력 … / 합계 …`
