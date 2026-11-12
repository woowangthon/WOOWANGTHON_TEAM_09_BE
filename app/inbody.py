# OCR로 읽은 인바디 결과지에서 키, 몸무게, 체지방률, 골격근량, 측정일을 찾는다.
#
# 결과지 한 장에 같은 값이 여러 번 나오는 것을 이용해 서로 대조한다 (InBody 270 결과지 기준, 좌표는 고정하지 않음)
#   몸무게(체중)·체지방률·골격근량:
#                       ① 그래프 줄 (라벨 오른쪽, 눈금 숫자들과 높이가 다른 숫자 하나)
#                       ② 신체변화 표 (라벨 오른쪽 숫자들 중 맨 오른쪽 = 최근)
#                       ③ "체지방률 37.5%"처럼 라벨과 값이 한 덩어리이거나, 라벨 오른쪽에 숫자가 하나뿐인 경우
#                          (체성분분석 표의 "체중 59.1 (45.0~60.8)": 괄호 안 정상 범위는 뺀다)
#   키: 머리말의 "156.9cm" (cm가 붙은 숫자. "신장" 라벨은 잘 안 읽혀서 cm로 찾고, cm를 못 읽었으면 라벨 바로 아래 숫자)
#   측정일: ① "2015.05.04" 같은 네 자리 연도 날짜 (검사일시 근처를 우선)  ② 신체변화 표의 마지막 날짜 "15.05.04"
# 값이 서로 다르면 ①을 쓰고 warnings에 알린다. 찾지 못한 값은 null + missing에 넣는다.
# 키·몸무게는 POST /info에 그대로 보낼 수 있도록 정수로 반올림한다 (156.9cm -> 157).

import logging
import math
import re
from datetime import date

from app.logs import log_event
from app.ocr import OcrLine
from app.week import today

# 라벨은 정확히 일치하는 글자로만 찾는다 (체지방"량"(kg)과 체지방"률"(%)이 한 글자 차이라 비슷한 글자 허용은 위험).
# 라벨 앞뒤에 다른 한글이 붙어 있으면 다른 항목이다 (적정"체중", "체중"조절, 저"체중").
logger = logging.getLogger(__name__)

FIELDS = {
    "weight": {"name": "몸무게", "labels": ("체중",), "range": (20.0, 250.0), "integer": True},
    "body_fat_percent": {"name": "체지방률", "labels": ("체지방률", "체지방율"), "range": (1.0, 75.0)},
    "skeletal_muscle_kg": {"name": "골격근량", "labels": ("골격근량",), "range": (5.0, 70.0)},
}
DATE_LABELS = ("검사일시", "검사일", "측정일", "측정일시", "testdate")
HEIGHT_LABELS = ("신장", "키")
HEIGHT_RANGE = (100.0, 230.0)

# 숫자 하나만 있는 글자: '37.5', '"22.1', '37.5%', '59.1kg', '59.1 (45.0~60.8)'(뒤 괄호 = 정상 범위, 뺀다).
# 앞뒤에 기호·단위만 허용한다 (체크박스 '□표준'이 '3표준'으로 읽히는 것처럼 글자가 붙어 있으면 숫자로 보지 않는다).
# 괄호가 붙은 숫자('(45.0', '60.8)')는 정상 범위가 쪼개져 읽힌 것이므로 숫자로 보지 않는다.
NUMBER_ONLY = re.compile(r"^[^\w가-힣()]{0,2}(\d{1,3}(?:\.\d{1,2})?)\s*(?:%|kg|㎏|cm)?"
                         r"(?:\s*\(\s*[\d.]+\s*[~\-–]\s*[\d.]+\s*\)?)?[^\w가-힣()]{0,2}$")
HEIGHT_CM = re.compile(r"(\d{2,3}(?:\.\d)?)\s*cm", re.IGNORECASE)   # 머리말의 '156.9cm' ('신장156.9cm'처럼 붙어도 찾음)
VALUE_AFTER_LABEL = re.compile(r"^[:：]?(\d{1,3}(?:\.\d{1,2})?)(%|kg)?$")   # 라벨 바로 뒤에 숫자(와 단위)만 있을 때
FULL_DATE = re.compile(r"((?:19|20)\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})")
SHORT_DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{2})$")                 # 신체변화 표의 '15.05.04'

TICK_MIN = 4   # 한 직선 위에 이만큼 이상 나란히 있는 숫자는 그래프 눈금일 수 있다 (6개 이상이면 눈금으로 본다)
TICK_LINE_PX = 2.5   # 눈금 직선에서 이만큼 안에 있으면 같은 직선 위로 본다


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def _number(text: str) -> float | None:
    m = NUMBER_ONLY.match(text.strip())
    return float(m.group(1)) if m else None


def _has_label(text: str, label: str) -> bool:
    # 라벨 앞뒤에 다른 한글이 붙지 않은 채로 들어 있는지 ("체중(kg)" O, "적정체중"·"체중조절" X)
    return re.search(rf"(?<![가-힣]){label}(?![가-힣])", text) is not None


def _row_numbers(label: OcrLine, lines: list[OcrLine]) -> list[tuple[OcrLine, float]]:
    # 라벨 오른쪽, 같은 줄 높이에 있는 숫자들 (왼쪽부터). 사이에 다른 한글 글자(다음 칸의 라벨, 예: 오른쪽 패널의
    # "체중조절")가 나오거나 간격이 갑자기 벌어지면 다른 칸이므로 거기서 끊는다.
    band = label.height * 0.6
    right = sorted((ln for ln in lines if ln is not label and ln.x0 > label.x1 and abs(ln.cy - label.cy) <= band),
                   key=lambda ln: ln.x0)
    stop = next((ln.x0 for ln in right if re.search(r"[가-힣]", ln.text)), math.inf)
    row = [(ln, v) for ln in right if ln.x0 < stop and (v := _number(ln.text)) is not None]
    if len(row) < 3:
        return row
    gaps = [b[0].x0 - a[0].x0 for a, b in zip(row, row[1:])]
    typical = sorted(gaps)[len(gaps) // 2]
    for i, gap in enumerate(gaps):
        if gap > max(2.5 * typical, label.height * 2):
            return row[:i + 1]
    return row


def _tick_line(numbers: list[tuple[OcrLine, float]]) -> tuple[set[int], tuple[float, float, float] | None]:
    # 그래프 눈금 찾기: 한 직선 위에 놓인 가장 큰 숫자 무리. 사진이 조금 기울어도 눈금들은 한 직선 위에 있고,
    # 그래프 값만 그 선에서 떨어져 있다. 무리가 6개 이상이거나, 4개 이상이면서 값이 왼쪽에서 오른쪽으로 대체로
    # 커지면 눈금으로 본다 (신체변화 표의 값들은 오르내려서 이 조건에 잘 맞지 않는다).
    # 반환: (눈금 숫자들의 id, 눈금 직선 (기준 x, 기준 y, 기울기))
    best, line = [], None
    for i, (a, _) in enumerate(numbers):
        for b, _ in numbers[i + 1:]:
            dx = b.cx - a.cx
            if abs(dx) < 20:
                continue
            slope = (b.cy - a.cy) / dx
            if abs(slope) > 0.18:   # 약 10도보다 가파르면 같은 줄이 아니다
                continue
            group = [(o, v) for o, v in numbers if abs(o.cy - (a.cy + slope * (o.cx - a.cx))) <= TICK_LINE_PX]
            if len(group) > len(best):
                best, line = group, (a.cx, a.cy, slope)
    if len(best) < TICK_MIN:
        return set(), None
    best.sort(key=lambda p: p[0].cx)
    rising = sum(q[1] > p[1] for p, q in zip(best, best[1:]))
    if len(best) >= 6 or rising >= 0.7 * (len(best) - 1):
        return {id(o) for o, _ in best}, line
    return set(), None


def _off_line(ln: OcrLine, line: tuple[float, float, float]) -> float:
    x, y, slope = line
    return abs(ln.cy - (y + slope * (ln.cx - x)))


def _candidates(field: dict, lines: list[OcrLine]) -> list[tuple[float, str]]:
    # (값, 어디서 찾았는지) 목록. "그래프"/"라벨 옆"이 "신체변화 표"보다 앞에 온다.
    found = []
    for label in lines:
        text = _norm(label.text)
        hit = next((lb for lb in field["labels"] if _has_label(text, lb)), None)
        if hit is None:
            continue
        # ③ 라벨과 값이 한 덩어리 ("체지방률 37.5%"). 라벨 뒤에 숫자와 단위만 있을 때만 인정한다
        # (OCR이 라벨과 그래프 눈금을 붙여 읽은 "체지방률(%)8.0" 같은 경우를 값으로 착각하지 않도록)
        if m := VALUE_AFTER_LABEL.match(text.split(hit, 1)[1]):
            found.append((float(m.group(1)), "라벨 옆"))
            continue
        numbers = _row_numbers(label, lines)
        if not numbers:
            continue
        ticks, line = _tick_line(numbers)
        rest_numbers = [(ln, v) for ln, v in numbers if id(ln) not in ticks]
        if ticks and rest_numbers:   # ① 그래프: 눈금이 아닌 숫자 (여럿이면 눈금 선에서 가장 멀리 떨어진 것)
            ln, v = max(rest_numbers, key=lambda p: _off_line(p[0], line))
            found.append((v, "그래프"))
        elif not ticks and len(numbers) >= 2:   # ② 신체변화 표: 맨 오른쪽이 최근
            ln, v = max(numbers, key=lambda p: p[0].x0)
            found.append((v, "신체변화 표"))
        elif not ticks:
            found.append((numbers[0][1], "라벨 옆"))
    lo, hi = field["range"]
    order = {"라벨 옆": 0, "그래프": 0, "신체변화 표": 1}
    return sorted([(v, src) for v, src in found if lo <= v <= hi], key=lambda c: order[c[1]])


def _height_candidates(lines: list[OcrLine]) -> list[tuple[float, str]]:
    # 키: cm가 붙은 숫자 (위쪽부터). 없으면 "신장" 라벨 바로 아래 숫자 (머리말은 라벨 줄 아래에 값 줄이 있다)
    lo, hi = HEIGHT_RANGE
    found = [(float(m.group(1)), "신장") for ln in lines for m in HEIGHT_CM.finditer(ln.text)
             if lo <= float(m.group(1)) <= hi]
    if found:
        return found
    for label in lines:
        if not any(_has_label(_norm(label.text), lb) for lb in HEIGHT_LABELS):
            continue
        below = [(ln.cy - label.cy, v) for ln in lines
                 if 0 < ln.cy - label.cy <= label.height * 2.5 and ln.x0 < label.x1 + label.height and ln.x1 > label.x0
                 and (v := _number(ln.text)) is not None and lo <= v <= hi]
        found += [(v, "신장") for _, v in sorted(below)[:1]]
    return found


def _valid_date(y: int, m: int, d: int) -> date | None:
    try:
        value = date(y, m, d)
    except ValueError:
        return None
    return value if date(2000, 1, 1) <= value <= today() else None


def _date_candidates(lines: list[OcrLine]) -> list[tuple[date, str]]:
    labels = [ln for ln in lines if any(lb in _norm(ln.text) for lb in DATE_LABELS)]
    full = []
    for ln in lines:
        for m in FULL_DATE.finditer(ln.text):
            if value := _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3))):
                # 검사일시 라벨과 가까울수록 앞 (라벨을 못 읽었으면 위에 있는 날짜부터)
                dist = min((abs(ln.cy - lb.cy) + abs(ln.x0 - lb.x0) for lb in labels), default=ln.y0)
                full.append((dist, value))
    found = [(value, "검사일시") for _, value in sorted(full, key=lambda p: p[0])]
    short = [ln for ln in lines if SHORT_DATE.match(ln.text.strip())]
    if short:   # 신체변화 표의 날짜들(한 줄) 중 맨 오른쪽 = 최근 (사진이 기울면 높이는 달라지므로 가로 위치만 본다)
        last = max(short, key=lambda ln: ln.x0)
        y, m, d = SHORT_DATE.match(last.text.strip()).groups()
        if value := _valid_date(2000 + int(y), int(m), int(d)):
            found.append((value, "신체변화 표"))
    return found


def _pick(name: str, candidates: list, warnings: list[str], fmt=str):
    # 첫 후보를 쓰고, 다른 출처의 값이 다르면 경고
    if not candidates:
        return None
    value, source = candidates[0]
    others = {fmt(v) for v, src in candidates[1:] if src != source and v != value}
    if others:
        warnings.append(f"{name}: 결과지에서 다른 값도 읽혔습니다 ({source} {fmt(value)} / 그 외 {', '.join(sorted(others))}). 확인해 주세요.")
    return value


def extract_inbody(lines: list[OcrLine]) -> dict:
    # POST /inbody/image 응답: 찾은 값 (못 찾으면 null), missing, warnings
    warnings: list[str] = []
    candidates = {"height": _height_candidates(lines), **{key: _candidates(field, lines) for key, field in FIELDS.items()}}
    result = {"height": _pick("키", candidates["height"], warnings)}
    result |= {key: _pick(field["name"], candidates[key], warnings) for key, field in FIELDS.items()}
    for key, field in (("height", {"integer": True}), *FIELDS.items()):
        if field.get("integer") and result[key] is not None:
            result[key] = int(result[key] + 0.5)   # POST /info의 height·weight는 정수
    candidates["inbody_measured_at"] = _date_candidates(lines)
    measured = _pick("측정일", candidates["inbody_measured_at"], warnings, fmt=lambda d: d.isoformat())
    result["inbody_measured_at"] = measured.isoformat() if measured else None
    names = {"height": "키", **{k: f["name"] for k, f in FIELDS.items()}, "inbody_measured_at": "측정일"}
    result["missing"] = [k for k in names if result[k] is None]
    if not lines:
        warnings.append("이미지에서 글자를 찾지 못했습니다. 결과지가 잘 보이게 다시 찍어 주세요.")
    elif len(result["missing"]) == len(names):
        warnings.append("인바디 결과지 항목을 찾지 못했습니다. 결과지 전체가 나오게 다시 찍어 주세요.")
    result["warnings"] = warnings
    # 개발자용: 최종 값과, 결과지 어디(출처)에서 어떤 값들이 후보로 읽혔는지
    log_event(logger, "ocr.extract", lines=len(lines),
              result={k: result[k] for k in names}, missing=result["missing"], warnings=warnings,
              candidates={k: [[str(v), src] for v, src in c] for k, c in candidates.items()})
    return result
