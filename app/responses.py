# API 응답 형식 (Pydantic 모델). 스웨거(/docs)와 /openapi.json에 응답 스키마로 나온다.
# 실제 응답도 이 모델로 걸러지므로, 여기에 없는 필드는 나가지 않는다 (내부 id 노출 방지).
#
# 시간 표기 규칙
# - 날짜: "YYYY-MM-DD"
# - 시각(하루 안): "HH:mm" (24시간제)
# - 일시: "YYYY-MM-DDTHH:MM:SS" (한국 시간, 시간대 표기 없음)

from pydantic import BaseModel, Field


# ---------- 공통 오류 ----------

class ErrorOut(BaseModel):
    detail: str = Field(description="오류 메시지 (사용자에게 보여줘도 되는 한국어 문장)",
                        examples=["로그인이 필요합니다."])


class ScheduleConflictItem(BaseModel):
    id: int = Field(description="겹치는 일정 id", examples=[7])
    name: str | None = Field(description="겹치는 일정 이름", examples=["동아리 모임"])
    start_term: str = Field(description="시작 일시", examples=["2026-11-17T18:00:00"])
    end_term: str = Field(description="끝 일시", examples=["2026-11-17T20:00:00"])
    created_by_ai: bool = Field(description="AI 운동 일정이면 true", examples=[False])


class ScheduleConflictDetail(BaseModel):
    message: str = Field(examples=["다른 일정과 시간이 겹칩니다."])
    conflicts: list[ScheduleConflictItem] = Field(description="겹치는 기존 일정")


class ScheduleConflictOut(BaseModel):
    detail: ScheduleConflictDetail


class BlockConflictItem(BaseModel):
    id: int = Field(description="겹치는 반복 일정 id", examples=[3])
    weekday: int = Field(description="요일 (1=월 ... 7=일)", examples=[4])
    start_time: str = Field(description="시작 시각 HH:mm", examples=["09:00"])
    end_time: str = Field(description="끝 시각 HH:mm", examples=["12:00"])
    name: str | None = Field(description="이름", examples=["자료구조"])


class BlockConflictDetail(BaseModel):
    message: str = Field(examples=["같은 요일의 다른 반복 일정과 시간이 겹칩니다."])
    conflicts: list[BlockConflictItem]


class BlockConflictOut(BaseModel):
    detail: BlockConflictDetail


class PlanConflictItem(BaseModel):
    date: str = Field(description="운동 날짜", examples=["2026-11-18"])
    workout: str = Field(description="저장하려던 운동 일정 이름", examples=["운동: 하체·복근"])
    schedule: str | None = Field(description="겹친 일정 이름", examples=["갑자기 생긴 약속"])


class PlanConflictDetail(BaseModel):
    message: str = Field(examples=["계획을 만드는 동안 추가된 일정과 겹칩니다. 다시 요청하세요."])
    conflicts: list[PlanConflictItem]


class PlanConflictOut(BaseModel):
    detail: PlanConflictDetail


# ---------- 사용자 / 건강 정보 ----------

class InfoOut(BaseModel):
    age: int = Field(description="나이 (만 나이)", examples=[21])
    height: int = Field(description="키 (cm)", examples=[175])
    weight: int = Field(description="몸무게 (kg)", examples=[72])
    sex: bool = Field(description="성별. true = 남성, false = 여성", examples=[True])
    experience: str | None = Field(description="헬스 경력 (자유 입력). 없으면 null", examples=["헬스 3개월"])
    body_fat_percent: float | None = Field(description="인바디 체지방률 (%). 입력 안 했으면 null", examples=[25.3])
    skeletal_muscle_kg: float | None = Field(description="인바디 골격근량 (kg). 입력 안 했으면 null", examples=[31.2])
    inbody_measured_at: str | None = Field(description="인바디 측정일 YYYY-MM-DD. 입력 안 했으면 null",
                                           examples=["2026-11-10"])
    nickname: str | None = Field(description="이름(닉네임). 없으면 null", examples=["근육왕"])
    goal: str | None = Field(description="운동 목표. 없으면 null", examples=["체중 감량"])
    uses_gym: bool | None = Field(description="헬스장 이용 여부. 없으면 null", examples=[True])
    busy_time_start: str | None = Field(description="매일 운동하기 어려운 시간대의 시작 HH:mm. 없으면 null", examples=["22:00"])
    busy_time_end: str | None = Field(
        description="매일 운동하기 어려운 시간대의 끝 HH:mm. 시작보다 이르면 다음 날 시각. 없으면 null",
        examples=["02:00"])


class UserOut(BaseModel):
    name: str = Field(description="로그인 아이디", examples=["cbnu2026"])
    created_at: str = Field(description="가입 시각 (DB 원본 값, UTC, ISO 8601)",
                            examples=["2026-11-09T03:00:00.123456+00:00"])
    department: str | None = Field(description="학과. 입력하지 않았으면 null (대시보드에서 '기타')", examples=["소프트웨어학부"])
    college: str | None = Field(description="단과대학 (학과로 서버가 채움). 학과가 없으면 null", examples=["전자정보대학"])
    info: InfoOut | None = Field(description="건강 정보. 아직 등록하지 않았으면 null")


class TokenOut(BaseModel):
    access_token: str = Field(description="JWT. 이후 요청 헤더에 'Authorization: Bearer <access_token>'으로 넣는다",
                              examples=["eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."])
    token_type: str = Field(description="항상 'bearer'", examples=["bearer"])


# ---------- 일정 ----------

class WorkoutExerciseOut(BaseModel):
    id: int = Field(description="운동 id. PUT /workout-exercise/{id}로 이수 체크할 때 쓴다", examples=[501])
    position: int = Field(description="그날 운동 순서 (1부터)", examples=[1])
    name: str = Field(description="운동 이름 (exercises.csv 목록 중 하나)", examples=["스쿼트"])
    sets: int | None = Field(description="세트 수. 세트가 없는 운동이면 null", examples=[3])
    reps: int | None = Field(description="세트당 반복 횟수. 시간 기반이면 null", examples=[12])
    duration_seconds: int | None = Field(
        description="시간(초). 세트가 있으면 세트당, 없으면 전체 시간. 횟수 기반이면 null", examples=[None])
    estimated_minutes: int = Field(description="예상 소요 시간(분). 세트 사이 휴식 포함", examples=[10])
    completed: bool = Field(description="이수 여부", examples=[False])


class ScheduleOut(BaseModel):
    id: int = Field(description="일정 id", examples=[16])
    name: str | None = Field(description="일정 이름. AI 운동 일정은 '운동: <초점>' 형식", examples=["운동: 하체·복근"])
    start_term: str = Field(description="시작 일시 (한국 시간, 시간대 표기 없음)", examples=["2026-11-18T19:00:00"])
    end_term: str = Field(description="끝 일시 (한국 시간, 시간대 표기 없음)", examples=["2026-11-18T19:50:00"])
    created_by_ai: bool = Field(description="POST /plan이 만든 운동 일정이면 true. 사용자가 바꿀 수 없다",
                                examples=[True])


class ScheduleDetailOut(ScheduleOut):
    exercises: list[WorkoutExerciseOut] = Field(
        description="그날의 운동 (created_by_ai가 true일 때만 있음, 사용자 일정이면 빈 배열)")


class WeeklyBlockOut(BaseModel):
    id: int = Field(description="반복 일정 id", examples=[3])
    weekday: int = Field(description="요일. 1=월, 2=화, 3=수, 4=목, 5=금, 6=토, 7=일", examples=[4])
    start_time: str = Field(description="시작 시각 HH:mm", examples=["09:00"])
    end_time: str = Field(description="끝 시각 HH:mm", examples=["12:00"])
    name: str | None = Field(description="이름", examples=["자료구조"])


class InbodyImageOut(BaseModel):
    body_fat_percent: float | None = Field(description="결과지에서 읽은 체지방률 (%). 못 찾으면 null", examples=[37.5])
    skeletal_muscle_kg: float | None = Field(description="결과지에서 읽은 골격근량 (kg). 못 찾으면 null", examples=[19.3])
    inbody_measured_at: str | None = Field(description="결과지에서 읽은 측정일 YYYY-MM-DD. 못 찾으면 null",
                                           examples=["2015-05-04"])
    missing: list[str] = Field(description="찾지 못한 필드 이름 (사용자에게 직접 입력받는다)", examples=[[]])
    warnings: list[str] = Field(
        description="사용자에게 보여줄 주의 문장 (결과지 두 곳의 값이 다름, 글자를 못 찾음 등). 없으면 빈 배열",
        examples=[["측정일: 결과지에서 다른 값도 읽혔습니다 (검사일시 2015-05-04 / 그 외 2015-01-12). 확인해 주세요."]])


# ---------- 운동 계획 (POST /plan) ----------

class InbodyOut(BaseModel):
    measured_at: str | None = Field(description="측정일 YYYY-MM-DD", examples=["2026-11-10"])
    body_fat_percent: float | None = Field(description="체지방률 (%)", examples=[25.3])
    skeletal_muscle_kg: float | None = Field(description="골격근량 (kg)", examples=[31.2])


class ProfileOut(BaseModel):
    age: int = Field(description="나이 (만 나이)", examples=[21])
    sex: str = Field(description="'남성' 또는 '여성'", examples=["남성"])
    target_group: str = Field(description="운동 지침상 대상: '아동·청소년'(18세 이하), '성인'(19~64세), '노인'(65세 이상)",
                              examples=["성인"])
    height_cm: int = Field(description="키 (cm)", examples=[175])
    weight_kg: int = Field(description="몸무게 (kg)", examples=[72])
    bmi: float = Field(description="체질량지수 = 몸무게 / 키(m)². 서버가 계산", examples=[23.5])
    bmi_category: str | None = Field(
        description="대한비만학회 기준: 저체중, 정상, 비만 전단계, 1단계 비만, 2단계 비만, 3단계 비만. "
                    "18세 이하는 성인 기준을 쓸 수 없어 null. 25 이상(1단계 비만부터)이면 고강도 운동을 빼고 계획",
        examples=["비만 전단계"])
    goal: str = Field(description="계획에 쓴 운동 목표 (요청의 goal, 없으면 info의 goal)", examples=["체중 감량"])
    experience: str | None = Field(description="헬스 경력 (info 값)", examples=["헬스 3개월"])
    uses_gym: bool | None = Field(description="헬스장 이용 여부 (info 값). null이면 알 수 없음", examples=[True])
    inbody: InbodyOut = Field(description="인바디 최신값 (입력 안 했으면 값이 모두 null)")


class PlanExerciseOut(BaseModel):
    name: str = Field(description="운동 이름", examples=["스쿼트"])
    sets: int | None = Field(description="세트 수. 세트가 없는 운동이면 null", examples=[3])
    reps: int | None = Field(description="세트당 반복 횟수. 시간 기반이면 null", examples=[12])
    duration_seconds: int | None = Field(
        description="시간(초). 세트가 있으면 세트당, 없으면 전체 시간. 횟수 기반이면 null", examples=[None])
    estimated_minutes: int = Field(description="예상 소요 시간(분). 세트 사이 휴식 포함", examples=[10])
    body_parts: list[str] = Field(description="운동 부위 (exercises.csv)", examples=[["하체"]])
    muscles: list[str] = Field(description="주요 근육 (exercises.csv)", examples=[["대퇴사두근(허벅지 앞)", "둔근(엉덩이)"]])


class PlanSessionOut(BaseModel):
    date: str = Field(description="날짜 YYYY-MM-DD", examples=["2026-11-18"])
    focus: str = Field(description="그날 운동 초점. 쉬는 날은 '휴식'", examples=["하체·복근"])
    start_time: str | None = Field(description="시작 시각 HH:mm. 쉬는 날은 null", examples=["19:00"])
    end_time: str | None = Field(description="끝 시각 HH:mm (= 시작 + total_minutes). 쉬는 날은 null", examples=["19:50"])
    total_minutes: int = Field(description="운동별 예상 시간의 합(분)", examples=[50])
    exercises: list[PlanExerciseOut] = Field(description="쉬는 날은 빈 배열")


class PlanDetailOut(BaseModel):
    found: bool = Field(description="참고 문서에 근거가 있으면 true. false면 sessions가 비어 있다", examples=[True])
    start_date: str = Field(description="계획 시작일 (그 주 월요일, 단 이미 지난 요일은 빼서 오늘일 수 있음)",
                            examples=["2026-11-16"])
    end_date: str = Field(description="계획 끝일 (그 주 일요일)", examples=["2026-11-22"])
    sessions: list[PlanSessionOut] = Field(description="날짜순 하루 단위 계획")
    precaution: str = Field(description="참고 문서에 근거한 주의사항 (한두 문장)",
                            examples=["비만 상태에서는 저강도부터 시작해 점차 강도와 시간을 늘리세요."])


class AdjustmentOut(BaseModel):
    date: str = Field(description="바뀐 날짜", examples=["2026-11-19"])   # 목: 09:00~12:00 자료구조와 겹쳐 옮김
    from_: str | None = Field(alias="from", description="AI가 정한 시작 시각 HH:mm", examples=["10:00"])
    to: str | None = Field(description="서버가 바꾼 시작 시각 HH:mm. null이면 그날을 뺐거나 휴식으로 바꿈",
                           examples=["12:00"])
    reason: str = Field(description="바꾼 이유 (사용자에게 보여줘도 되는 문장)", examples=["일정과 겹치거나 운동 가능 시간을 벗어나 옮김"])

    model_config = {"populate_by_name": True, "serialize_by_alias": True}


class SavedWorkoutOut(ScheduleOut):
    exercises: list[WorkoutExerciseOut] = Field(description="저장된 그날의 운동 (id로 이수 체크)")


class SourceOut(BaseModel):
    id: int = Field(description="참고 문서 번호", examples=[1])
    source: str = Field(description="문서 이름", examples=["신체활동 지침서"])
    targets: list[str] = Field(description="문서 조각의 대상 (성인, 아동·청소년, 공통 등)", examples=[["성인"]])
    pinned: bool = Field(description="대상의 핵심 지침이라 검색 없이 항상 넣은 조각이면 true", examples=[True])
    score: float | None = Field(description="검색 점수 (BM25). pinned면 null", examples=[None])
    text: str = Field(description="문서 조각 원문", examples=[
        "신체활동 지침\n중강도 유산소 신체활동을 일주일에 150-300분 또는 고강도 유산소 신체활동을 일주일에\n75-150분 해야 합니다."])


class PlanOut(BaseModel):
    profile: ProfileOut = Field(description="계획에 쓴 사용자 정보 (DB info + 요청의 goal, 서버가 계산한 BMI)")
    plan: PlanDetailOut = Field(description="운동 계획")
    adjusted: list[AdjustmentOut] = Field(description="서버가 AI 계획을 고친 내역 (일정 충돌, 기간 밖 날짜 등). 없으면 빈 배열")
    saved: list[SavedWorkoutOut] = Field(description="schedule에 저장된 운동 일정 (운동하는 날만)")
    replaced: int = Field(description="regenerate로 지운 이전 AI 운동 일정 수 (regenerate가 아니면 0)", examples=[0])
    sources: list[SourceOut] = Field(description="AI가 참고한 문서 조각 (근거 표시용)")


class PlanDeleteOut(BaseModel):
    week_start: str = Field(description="삭제한 주의 월요일", examples=["2026-11-16"])
    week_end: str = Field(description="삭제한 주의 일요일", examples=["2026-11-22"])
    deleted: int = Field(description="삭제한 AI 운동 일정 수 (0이면 그 주에 플랜이 없었음)", examples=[5])


# ---------- 주간 리포트 ----------

class RateOut(BaseModel):
    planned: int = Field(description="계획된 운동 종목 수", examples=[8])
    completed: int = Field(description="완료한 종목 수", examples=[5])
    rate: int | None = Field(description="수행률(%) = completed / planned × 100 (반올림). 계획이 없으면 null",
                             examples=[62])


class ReportSessionOut(BaseModel):
    schedule_id: int = Field(description="운동 일정 id (GET /schedule/{id}로 자세히 보기)", examples=[16])
    name: str | None = Field(description="운동 일정 이름", examples=["운동: 하체·복근"])
    start_time: str = Field(description="시작 시각 HH:mm", examples=["19:00"])
    planned: int = Field(description="이 일정의 계획 종목 수", examples=[5])
    completed: int = Field(description="이 일정에서 완료한 종목 수", examples=[3])


class ReportDayOut(RateOut):
    date: str = Field(description="날짜 YYYY-MM-DD", examples=["2026-11-18"])
    weekday: str = Field(description="요일 한 글자 (월~일)", examples=["수"])
    sessions: list[ReportSessionOut] = Field(description="그날의 AI 운동 일정")


class ReportOut(BaseModel):
    week_start: str = Field(description="주의 월요일", examples=["2026-11-16"])
    week_end: str = Field(description="주의 일요일", examples=["2026-11-22"])
    days: list[ReportDayOut] = Field(description="월~일 7개 (계획 없는 날은 planned 0, rate null)")
    total: RateOut = Field(description="주 전체 (앞으로 할 운동 포함)")
    so_far: RateOut = Field(description="오늘까지의 운동만 센 수행률 (주 중간에 진행 상황을 볼 때)")
    workout_days: int = Field(description="운동이 계획된 날 수", examples=[4])
    completed_days: int = Field(description="계획된 종목을 모두 완료한 날 수", examples=[2])


# ---------- 대시보드 (단과대학 경쟁) ----------

class CollegeRankOut(BaseModel):
    rank: int | None = Field(description="순위 (수행률 높은 순, 같으면 같은 순위). 그 주에 계획이 없거나 '기타'면 null",
                             examples=[1])
    college: str = Field(description="단과대학 이름. 학과를 입력하지 않은 사용자는 '기타'", examples=["전자정보대학"])
    members: int = Field(description="가입한 소속 학생 수 (운동 계획이 없는 학생 포함)", examples=[12])
    planned: int = Field(description="소속 학생들의 계획 운동 종목 수 합 (오늘까지)", examples=[96])
    completed: int = Field(description="그중 완료한 종목 수 합", examples=[71])
    rate: float | None = Field(description="수행률(%) = completed / planned × 100 (소수 첫째 자리). 계획이 없으면 null",
                               examples=[74.0])


class DashboardOut(BaseModel):
    week_start: str = Field(description="주의 월요일", examples=["2026-11-16"])
    week_end: str = Field(description="주의 일요일", examples=["2026-11-22"])
    my_college: str = Field(description="내 단과대학 (학과가 없으면 '기타')", examples=["전자정보대학"])
    colleges: list[CollegeRankOut] = Field(
        description="모든 단과대학 (순위순, 계획 없는 단과대학은 뒤, 맨 끝에 '기타')")
