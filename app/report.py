# 주간 운동 리포트 (AI가 계획한 운동의 종목별 이수 여부로 계산).
#
# - weekly_report: GET /report 응답. 요일별 계획·완료 종목 수와 수행률
# - history_text: 다음 계획 프롬프트의 [지난주 운동 수행] 부분 (요일별·시간대별 수행률)
# - college_dashboard: GET /dashboard 응답. 단과대학별 수행률 순위

from datetime import date, timedelta

from app.db import fetch_ai_workouts, fetch_college_workouts
from app.departments import COLLEGES, OTHER
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


def college_dashboard(day: date, my_college: str | None) -> dict:
    # day가 속한 주(월~일)의 단과대학별 수행률 = 소속 학생들의 (완료 종목 수 합) / (계획 종목 수 합).
    # 아직 오지 않은 날의 운동은 세지 않는다 (주 중간에 앞으로 할 운동 때문에 수행률이 낮아 보이지 않도록).
    # 학과를 입력하지 않은 사용자는 "기타"로 묶고, 순위에는 넣지 않는다.
    # 운동한 학생(active_members): 그 기간에 운동을 한 종목이라도 완료한 학생 수
    monday, sunday = week_bounds(day)
    colleges, workouts = fetch_college_workouts(monday, min(sunday, today()))
    names = COLLEGES + [OTHER]
    stats = {name: {"college": name, "members": 0, "active_members": 0, "planned": 0, "completed": 0}
             for name in names}
    group = lambda college: stats.get(college) or stats[OTHER]   # CSV에서 빠진 옛 단과대학도 기타로
    for college in colleges.values():
        group(college)["members"] += 1
    active = set()   # 한 종목이라도 완료한 사용자
    for w in workouts:
        s = group(colleges.get(w["author"]))
        s["planned"] += len(w["completed"])
        s["completed"] += sum(w["completed"])
        if any(w["completed"]):
            active.add(w["author"])
    for user_id in active:
        group(colleges.get(user_id))["active_members"] += 1
    for s in stats.values():
        s["rate"] = round(s["completed"] * 100 / s["planned"], 1) if s["planned"] else None

    ranked = sorted((stats[n] for n in COLLEGES),
                    key=lambda s: (s["rate"] is None, -(s["rate"] or 0), -s["completed"]))
    for i, s in enumerate(ranked):   # 수행률이 같으면 같은 순위 (1, 2, 2, 4 ...). 계획이 없으면 순위 없음
        same = i and ranked[i - 1]["rate"] == s["rate"]
        s["rank"] = None if s["rate"] is None else (ranked[i - 1]["rank"] if same else i + 1)
    stats[OTHER]["rank"] = None
    return {
        "week_start": monday.isoformat(),
        "week_end": sunday.isoformat(),
        "my_college": my_college or OTHER,
        "active_users": len(active),
        "colleges": ranked + [stats[OTHER]],
    }
