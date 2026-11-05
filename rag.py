"""전처리된 텍스트 파일(preprocess/result/*.txt) 기반의 간단한 RAG 검색기.

외부 의존성 없이 BM25 + 문자 bigram 토크나이저로 한국어 문서를 검색한다.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path(__file__).parent / "preprocess" / "result"
CHUNK_SIZE = 500     # 청크 최대 글자 수
CHUNK_OVERLAP = 100  # 청크 간 겹치는 글자 수


@dataclass
class Chunk:
    source: str
    text: str


def _tokenize(text: str) -> list[str]:
    """공백/특수문자로 단어를 나눈 뒤 문자 bigram으로 쪼갠다 (한국어 조사 변형에 강함)."""
    tokens = []
    for word in re.findall(r"[0-9A-Za-z가-힣]+", text.lower()):
        if len(word) == 1:
            tokens.append(word)
        else:
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
    return tokens


def _split_chunks(text: str) -> list[str]:
    """줄 단위로 이어붙여 CHUNK_SIZE 정도의 청크를 만들고, 앞 청크 끝부분을 겹치게 한다."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > CHUNK_SIZE:
            chunks.append(current)
            current = current[-CHUNK_OVERLAP:]
        current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    return chunks


class BM25Retriever:
    def __init__(self, data_dir: Path = DATA_DIR, k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.chunks: list[Chunk] = []
        for path in sorted(data_dir.glob("*.txt")):
            text = path.read_text(encoding="utf-8")
            self.chunks.extend(Chunk(path.stem, c) for c in _split_chunks(text))

        self.doc_tfs = [Counter(_tokenize(c.text)) for c in self.chunks]
        self.doc_lens = [sum(tf.values()) for tf in self.doc_tfs]
        self.avgdl = (sum(self.doc_lens) / len(self.doc_lens)) if self.doc_lens else 0.0

        df = Counter()
        for tf in self.doc_tfs:
            df.update(tf.keys())
        n = len(self.chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, top_k: int = 4) -> list[tuple[Chunk, float]]:
        q_tokens = set(_tokenize(query))
        scored = []
        for chunk, tf, dl in zip(self.chunks, self.doc_tfs, self.doc_lens):
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
