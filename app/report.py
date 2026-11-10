# 주간 운동 리포트 (AI가 계획한 운동의 종목별 이수 여부로 계산).
#
# - weekly_report: GET /report 응답. 요일별 계획·완료 종목 수와 수행률
# - history_text: 다음 계획 프롬프트의 [지난주 운동 수행] 부분 (요일별·시간대별 수행률)

from datetime import date, timedelta

from app.db import fetch_ai_workouts
from app.week import WEEKDAY_NAMES, days_between, today, week_bounds

# 시간대 구분 (시작 시각 기준)
TIME_BANDS = (("오전", 0, 12), ("오후", 12, 18), ("저녁", 18, 24))


def _rate(done: int, planned: int) -> int | None:
    # 수행률(%). 계획이 없으면 None.
    return round(done * 100 / planned) if planned else None


def _summary(workouts: list[dict]) -> dict:
    planned = sum(len(w["completed"]) for w in workouts)
    done = sum(sum(w["completed"]) for w in workouts)
    return {"planned": planned, "completed": done, "rate": _rate(done, planned)}


def weekly_report(user_id: int, day: date) -> dict:
    # day가 속한 주(월~일)의 리포트. 수행률 = 완료한 종목 수 / 계획된 종목 수.
    monday, sunday = week_bounds(day)
    workouts = fetch_ai_workouts(user_id, monday, sunday)
    now = today()

    days = []
    for d in days_between(monday, sunday):
        todays = [w for w in workouts if w["start"].date() == d]
        days.append({
            "date": d.isoformat(),
            "weekday": WEEKDAY_NAMES[d.weekday()],
            **_summary(todays),
            "sessions": [
                {"schedule_id": w["id"], "name": w["name"], "start_time": w["start"].strftime("%H:%M"),
                 "planned": len(w["completed"]), "completed": sum(w["completed"])}
                for w in todays
            ],
        })

    return {
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "days": days,
        "total": _summary(workouts),                                         # 주 전체 (앞으로 할 운동 포함)
        "so_far": _summary([w for w in workouts if w["start"].date() <= now]),   # 오늘까지만
        "workout_days": sum(1 for d in days if d["planned"]),
        "completed_days": sum(1 for d in days if d["planned"] and d["completed"] == d["planned"]),
    }


def history_text(user_id: int, monday: date) -> str:
    # monday가 속한 주의 바로 전 주 수행 기록을 프롬프트용 문장으로.
    prev_monday, prev_sunday = week_bounds(monday - timedelta(days=7))
    workouts = fetch_ai_workouts(user_id, prev_monday, prev_sunday)
    head = f"- 기간: {prev_monday.isoformat()} ~ {prev_sunday.isoformat()}"
    if not workouts:
        return head + "\n- 기록 없음"

    def fmt(ws: list[dict]) -> str:
        s = _summary(ws)
        return f"{s['rate']}% ({s['completed']}/{s['planned']}종목)"

    by_day = [
        f"{WEEKDAY_NAMES[d.weekday()]} {fmt(ws)}"
        for d in days_between(prev_monday, prev_sunday)
        if (ws := [w for w in workouts if w["start"].date() == d])
    ]
    by_band = [
        f"{name}({lo:02d}~{hi:02d}시) {fmt(ws)}"
        for name, lo, hi in TIME_BANDS
        if (ws := [w for w in workouts if lo <= w["start"].hour < hi])
    ]
    return "\n".join([
        head,
        f"- 전체 수행률: {fmt(workouts)}",
        f"- 요일별: {', '.join(by_day)}",
        f"- 시간대별: {', '.join(by_band)}",
        "- 수행률이 낮았다면 운동량을 줄이고, 수행률이 낮은 요일·시간대는 피하라.",
    ])
