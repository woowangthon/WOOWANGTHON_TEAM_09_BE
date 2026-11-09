"""preprocess/original-result/*.txt -> preprocess/result/*.txt 정제.

줄 단위 삭제만 한다 (문장 수정·추가 없음). 남기는 대상: 아동·청소년, 성인, 공통 (청소년~중장년).

    python preprocess/clean.py                 # 정제본 생성
    python preprocess/clean.py --log <폴더>    # 지운 줄(줄 번호, 이유, 내용)도 문서별로 저장

문서를 추가하면 app/rag/documents.py의 섹션 규칙을 먼저 넣고, 필요하면 아래 DOCS에 문서별 삭제 규칙을 추가한다.
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent              # preprocess/
sys.path.insert(0, str(ROOT.parent))                 # server/ (app 패키지)
from app.rag.documents import SPLITTERS, split_default, COMMON  # noqa: E402

SRC, DST = ROOT / "original-result", ROOT / "result"
KEEP_TARGETS = {"아동·청소년", "성인", COMMON}   # 노인·유아·임산부·만성질환자·장애인 전용 내용은 뺀다

R = re.compile
# ---------- 모든 문서 공통으로 지우는 줄 ----------
GLOBAL_DROP = [
    R(r"^\s*$"),                                   # 빈 줄
    R(r"^\s*(https?://|www\.)\S+\s*$"),            # URL만 있는 줄
    R(r"^\s*(DOI:|http://dx\.doi\.org/|pISSN)"),   # DOI·ISSN
    R(r"^\s*[•·]\s*$"),                            # 글머리 기호만 있는 줄
]
PAGE_SPREAD = R(r"^\s*(\d{1,3}) (\d{1,3})\s*$")   # "10 11" 같은 펼침면 쪽 번호

# ---------- 문서별 규칙 ----------
# ranges: (시작 줄 패턴, 끝 줄 패턴 또는 None=문서 끝[, 끝 줄도 지울지]). 기본은 시작 줄부터 끝 줄 "직전"까지 지운다.
DOCS = {
    "WHO 신체활동 및 좌식행동 가이드라인": dict(
        ranges=[(R(r"^규칙적인 신체활동은 심혈관 질환"), R(r"^(개요 )?어린이(와| 및) 청소년 \(5세")),  # 배경·방법·근거(개발 과정)
                (R(r"^근거의 확실성 평가"), None)],                                              # 근거 평가·연구 필요성·전파 계획
        drop=[]),
    "american-college-of-sports-medicine-position-stand": dict(
        ranges=[(R(r"^HYPERTROPHY, PHYSICAL FUNCTION, RESISTANCE TRAINING"), R(r"^E\s*$")),   # 주제어·교신저자·투고일·저작권
                (R(r"^METHODS\s*$"), R(r"^RESULTS\s*$")),                                      # 연구 방법
                # 근거 표(✔/?/✖, QoE). 칸 구조가 없어 읽을 수 없고, 같은 결과가 본문(요약·고찰)에 있다
                (R(r"^TABLE 3\."), R(r"^CTRL, nonexercising control; ND, no data"), True),
                (R(r"^TABLE 4\. Resistance training"), R(r"^\d{3} Official Journal of the American College")),
                (R(r"^TABLE 4\. Continued"), R(r"^CTRL, nonexercising control; ND, no data"), True),
                (R(r"^TABLE 5\."), R(r"^CTRL, nonexercising control; ND, no data"), True)],
        drop=[R(r"^SPECIAL COMMUNICATIONS\s*$"),
              R(r"^RESISTANCE TRAINING PRESCRIPTION Medicine & Science in Sports & Exercise"),
              R(r"^\d{3} Official Journal of the American College of Sports Medicine"),
              R(r"^\s*QoE = \d+%\s*$"), R(r"^\s*[✔✖?]\s*$"), R(r"^\s*ND\s*$"),
              R(r"^\s*[↑↓?] \d+ reviews?; n = [\d,]+\s*$")]),                                  # 표 칸 조각
    "acsms-complete-guide-fitness-health-sample-download": dict(
        # 그림 안 글자(축 숫자, 짧은 라벨). 그림 설명(FIGURE ...)이나 본문(60자 이상인 줄)이 나오면 끝
        ranges=[(R(r"^E6843/ACSM/"), R(r"^(FIGURE \d|.{60,})"))],
        drop=[R(r"^ACSM’s Complete Guide to Fitness & Health\d+\s*$"),
              R(r"^Making Healthy Lifestyle Choices \d+\s*$"),
              R(r"^Source: ")]),
    "jomes-32-1-1": dict(
        ranges=[(R(r"^METHODS\s*$"), R(r"^1\. Diagnosis of obesity")),                         # 지침 개발 방법
                (R(r"^6\. Pharmacotherapy"), R(r"^8\. Obesity in the elderly")),                # 약물·수술 권고
                (R(r"^Literature review"), R(r"^(Table 2\. Diagnostic criteria|DIAGNOSTIC CRITERIA FOR OBESITY)")),
                # 약물·수술 본문. 중간에 끼어 있는 Table 5(식사요법 요약)는 남긴다
                (R(r"^PHARMACOTHERAPY\s*$"), R(r"^Table 5\. Summary of the characteristics of diverse diet")),
                (R(r"^Obesity pharmacotherapy is recommended as an additional"), R(r"^NEW TOPICS INCLUDED"))],
        drop=[R(r"^Kim KK, et al\. Clinical Practice Guidelines for Obesity by KSSO\s*$"),
              R(r"^J Obes Metab Syndr 2023;32:1-24"), R(r"https://www\.jomes\.org")]),
    "한국인을 위한 달리기 가이드라인": dict(
        ranges=[],
        drop=[R(r"^안전하고 영리한 한국인의 달리기 지침서\s*$"), R(r"^\s*\d{1,2}\s*$")]),          # 머리말, 쪽 번호
    "성인을 위한 신체활동 가이드라인 및 운동 강도에 대한 고찰": dict(
        ranges=[(R(r"^❚ Review"), R(r"^서론\. Ⅰ")),                                             # 학술지 정보·저자·영문 초록
                (R(r"^References\s*$"), None)],                                                # 참고문헌
        drop=[R(r"^운동과학 년 제 권 제 호, 2015")]),
}

GUIDELINE = "신체활동 지침서"
GUIDE_TITLE = "한국인을 위한 신체활동 지침서"


def line_targets(stem, text):
    """원문 줄마다 (분리 규칙에 포함됐는지, 대상)."""
    lines = text.splitlines()
    sections = SPLITTERS.get(stem, split_default)(text)
    info = [None] * len(lines)           # None = 분리 규칙이 버린 줄
    p = 0
    for sec in sections:
        for sl in sec.lines:
            while lines[p] != sl:
                p += 1
            info[p] = sec.targets
            p += 1
    return lines, info


def clean(stem, text):
    lines, info = line_targets(stem, text)
    keep = [t is not None and bool(KEEP_TARGETS & set(t)) for t in info]
    reason = ["대상 아님/분리 규칙 제외" if not k else "" for k in keep]
    cfg = DOCS.get(stem, dict(ranges=[], drop=[]))

    # 구간 삭제
    for start, end, *opt in cfg["ranges"]:
        include_end = bool(opt and opt[0])
        i = 0
        while i < len(lines):
            if keep[i] and start.search(lines[i].strip()):   # 이미 지워진 줄(목차 등)에서는 구간을 시작하지 않는다
                j = i
                while j < len(lines) and not (end and j > i and end.search(lines[j].strip())):
                    if keep[j]:
                        keep[j], reason[j] = False, f"구간: {start.pattern[:30]}"
                    j += 1
                if include_end and j < len(lines) and keep[j]:
                    keep[j], reason[j] = False, f"구간: {start.pattern[:30]}"
                i = j + 1
            else:
                i += 1

    # 줄 삭제
    for i, line in enumerate(lines):
        if not keep[i]:
            continue
        s = line.strip()
        m = PAGE_SPREAD.match(s)
        if any(r.search(line) for r in GLOBAL_DROP) or (m and int(m.group(2)) == int(m.group(1)) + 1) \
                or any(r.search(s) for r in cfg["drop"]):
            keep[i], reason[i] = False, "잡음 줄"

    # 신체활동 지침서: 대상 판단에 쓰는 페이지 머리말(제목+쪽 번호)은 남은 내용이 있는 쪽이면 유지
    if stem == GUIDELINE:
        headers, page_kept, page = {}, {}, None
        i = 0
        while i < len(lines):
            if lines[i].strip() == GUIDE_TITLE and i + 1 < len(lines) and lines[i + 1].strip().isdigit():
                page = int(lines[i + 1].strip()); headers[page] = (i, i + 1); i += 2; continue
            if keep[i] and page is not None:
                page_kept[page] = True
            i += 1
        for pg, (a, b) in headers.items():
            if page_kept.get(pg):
                keep[a] = keep[b] = True
                reason[a] = reason[b] = ""
    removed = [(i + 1, reason[i], lines[i]) for i in range(len(lines)) if not keep[i]]
    return "\n".join(l for l, k in zip(lines, keep) if k) + "\n", removed


def main() -> None:
    parser = argparse.ArgumentParser(description="original-result/ -> result/ 정제")
    parser.add_argument("--log", type=Path, help="지운 줄 목록을 저장할 폴더")
    args = parser.parse_args()

    DST.mkdir(exist_ok=True)
    if args.log:
        args.log.mkdir(parents=True, exist_ok=True)
    for path in sorted(SRC.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        out, removed = clean(path.stem, text)
        (DST / path.name).write_text(out, encoding="utf-8")
        if args.log:
            (args.log / path.name).write_text("\n".join(f"{n}\t{r}\t{l}" for n, r, l in removed), encoding="utf-8")
        print(f"{path.stem[:40]:42s} {len(text.splitlines()):5d}줄 -> {len(out.splitlines()):5d}줄  ({len(text):,}자 -> {len(out):,}자)")


if __name__ == "__main__":
    main()
