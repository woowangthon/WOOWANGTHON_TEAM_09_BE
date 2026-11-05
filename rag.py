"""전처리된 텍스트 파일(preprocess/result/*.txt) 기반의 간단한 RAG 검색기.

- 문서를 섹션 단위로 나누고 각 섹션에 대상(성인, 노인, 임산부 등)을 붙인다.
- 검색 시 사용자 대상 + 공통 섹션만 후보로 삼아 다른 대상의 지침이 섞이지 않게 한다.
- 외부 의존성 없이 BM25 + 문자 bigram 토크나이저로 한국어 문서를 검색한다.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "preprocess" / "result"
CHUNK_SIZE = 1200    # 청크 최대 글자 수 (지침 한 페이지가 한 청크에 들어가도록)
CHUNK_OVERLAP = 150  # 섹션이 길어 여러 청크로 나눌 때 겹치는 글자 수

COMMON = "공통"
TARGETS = ("유아", "아동·청소년", "성인", "노인", "임산부", "만성질환자", "장애인")


@dataclass
class Chunk:
    source: str
    targets: tuple[str, ...]  # 이 청크가 해당하는 대상. 일반 내용은 ("공통",)
    text: str
    core: bool = False        # 해당 대상의 핵심 신체활동 지침(권장량)이 담긴 청크


@dataclass
class Section:
    targets: tuple[str, ...]
    lines: list[str]


# ---------- 대상 분류 ----------

def classify_heading(heading: str) -> tuple[str, ...]:
    """섹션 제목으로 대상을 정한다. 더 구체적인 대상(장애, 질환, 임신)을 먼저 본다."""
    h = heading.upper()
    if "장애" in h or "DISABILITY" in h:
        return ("장애인",)
    if "환자" in h or "질환" in h or "CHRONIC" in h:
        return ("만성질환자",)
    if "임산부" in h or "PREGNANT" in h:
        return ("임산부",)
    if "성인 및 노인" in h:
        return ("성인", "노인")
    if "노인" in h or "OLDER" in h:
        return ("노인",)
    if "어린이" in h or "청소년" in h or "CHILDREN" in h:
        return ("아동·청소년",)
    if "유아" in h:
        return ("유아",)
    if "성인" in h or "ADULTS" in h:
        return ("성인",)
    return (COMMON,)


# ---------- 문서별 섹션 분리 ----------
# 문서마다 제목 형식이 달라서 문서별 규칙을 둔다. 새 문서는 기본 규칙(전체를 "공통")을 따른다.

# 한국인을 위한 신체활동 지침서: 대상 표시가 페이지 끝에 있어 목차 기준 페이지 범위로 나눈다.
GUIDELINE_PAGE_TARGETS = [
    (26, 29, "유아"),
    (30, 33, "아동·청소년"),
    (34, 37, "성인"),
    (38, 41, "노인"),
    (42, 47, "임산부"),
    (48, 53, "만성질환자"),
    (54, 59, "장애인"),
]
GUIDELINE_FIRST_PAGE = 24   # 이전 페이지(표지, 목차)는 제외
GUIDELINE_LAST_PAGE = 79    # 이후 페이지(관련 웹사이트 목록)는 제외


def split_guideline(text: str) -> list[Section]:
    sections: list[Section] = []
    lines = text.splitlines()
    i, page = 0, None
    current: list[str] = []

    def flush():
        if page is not None and current and GUIDELINE_FIRST_PAGE <= page <= GUIDELINE_LAST_PAGE:
            target = next((t for lo, hi, t in GUIDELINE_PAGE_TARGETS if lo <= page <= hi), COMMON)
            sections.append(Section((target,), list(current)))

    while i < len(lines):
        line = lines[i].strip()
        # 페이지 머리말: "한국인을 위한 신체활동 지침서" 다음 줄이 페이지 번호
        if line == "한국인을 위한 신체활동 지침서" and i + 1 < len(lines) and lines[i + 1].strip().isdigit():
            flush()
            page, current = int(lines[i + 1].strip()), []
            i += 2
            continue
        current.append(lines[i])
        i += 1
    flush()
    return sections


# 운동 (국가건강정보포털): "1. 성인의 운동" 같은 번호 제목으로 나눈다.
EXERCISE_HEADING = re.compile(r"^\d+\.\s+[가-힣A-Za-z]")
EXERCISE_NOISE = re.compile(r"^\d+\. \d+\. \d+\. 오후|^https?://|국가건강정보포털")


def split_exercise(text: str) -> list[Section]:
    sections = [Section((COMMON,), [])]
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("참고문헌"):
            break  # 이후는 참고문헌 목록
        if EXERCISE_NOISE.search(stripped):
            continue
        if EXERCISE_HEADING.match(stripped):
            sections.append(Section(classify_heading(stripped), []))
        sections[-1].lines.append(line)
    return sections


# WHO 가이드라인: 대문자 제목(ADULTS, OLDER ADULTS 등)으로 나눈다.
WHO_HEADING = re.compile(r"^[A-Z][A-Z ,()–\-0-9]{4,}$")
WHO_SUBHEADINGS = {"GOOD PRACTICE", "STATEMENTS", "REPLACE"}  # 섹션 안의 소제목
WHO_START = "EVERY MOVE COUNTS"    # 앞부분(표지, 저작권, 목차)은 제외
WHO_DROP = {"ACKNOWLEDGEMENTS"}    # 감사의 글은 제외


def split_who(text: str) -> list[Section]:
    sections: list[Section] = []
    started, dropping = False, False
    for line in text.splitlines():
        stripped = line.strip()
        if not started:
            started = stripped == WHO_START
            if not started:
                continue
        if WHO_HEADING.match(stripped) and stripped not in WHO_SUBHEADINGS:
            dropping = stripped in WHO_DROP
            if not dropping:
                sections.append(Section(classify_heading(stripped), []))
        if not dropping and sections:
            sections[-1].lines.append(line)
    return sections


SPLITTERS = {
    "신체활동 지침서": split_guideline,
    "운동": split_exercise,
    "WHO 가이드라인": split_who,
}


def split_default(text: str) -> list[Section]:
    return [Section((COMMON,), text.splitlines())]


# ---------- 청크 ----------

def _split_chunks(lines: list[str]) -> list[str]:
    """줄 단위로 이어붙여 CHUNK_SIZE 정도의 청크를 만들고, 앞 청크 끝부분을 겹치게 한다."""
    lines = [line.strip() for line in lines if line.strip()]
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > CHUNK_SIZE:
            chunks.append(current)
            current = current[-CHUNK_OVERLAP:]
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


def _is_core(source: str, targets: tuple[str, ...], text: str) -> bool:
    """신체활동 지침서에서 대상별 권장량("신체활동 지침" 소제목)이 담긴 청크."""
    return source == "신체활동 지침서" and COMMON not in targets and "신체활동 지침\n" in text


def load_chunks(data_dir: Path = DATA_DIR) -> list[Chunk]:
    chunks = []
    for path in sorted(data_dir.glob("*.txt")):
        splitter = SPLITTERS.get(path.stem, split_default)
        for section in splitter(path.read_text(encoding="utf-8")):
            for text in _split_chunks(section.lines):
                chunks.append(Chunk(path.stem, section.targets, text, _is_core(path.stem, section.targets, text)))
    return chunks


# ---------- 검색 ----------

def _tokenize(text: str) -> list[str]:
    """공백/특수문자로 단어를 나눈 뒤 문자 bigram으로 쪼갠다 (한국어 조사 변형에 강함)."""
    tokens = []
    for word in re.findall(r"[0-9A-Za-z가-힣]+", text.lower()):
        if len(word) == 1:
            tokens.append(word)
        else:
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


class BM25Retriever:
    def __init__(self, data_dir: Path = DATA_DIR, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.chunks = load_chunks(data_dir)

        self.doc_tfs = [Counter(_tokenize(c.text)) for c in self.chunks]
        self.doc_lens = [sum(tf.values()) for tf in self.doc_tfs]
        self.avgdl = (sum(self.doc_lens) / len(self.doc_lens)) if self.doc_lens else 0.0

        df = Counter()
        for tf in self.doc_tfs:
            df.update(tf.keys())
        n = len(self.chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def core_chunks(self, target: str) -> list[Chunk]:
        """대상의 핵심 지침(권장량) 청크. 검색 점수와 상관없이 항상 참고 문서에 넣는다."""
        return [c for c in self.chunks if c.core and target in c.targets]

    def search(
        self, query: str, top_k: int = 4, targets: set[str] | None = None, exclude: list[Chunk] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """targets가 있으면 그 대상이나 "공통"인 청크만 검색한다."""
        allowed = (targets | {COMMON}) if targets else None
        excluded = {id(c) for c in exclude or []}
        q_tokens = set(_tokenize(query))
        scored = []
        for chunk, tf, dl in zip(self.chunks, self.doc_tfs, self.doc_lens):
            if id(chunk) in excluded:
                continue
            if allowed is not None and not allowed.intersection(chunk.targets):
                continue
            score = 0.0
            for t in q_tokens:
                f = tf.get(t)
                if not f:
                    continue
                score += self.idf[t] * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                )
            if score > 0:
                scored.append((chunk, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]
