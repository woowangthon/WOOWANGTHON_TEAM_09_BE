"""BM25 검색기. 외부 의존성 없이 문자 bigram 토크나이저로 한국어·영어 문서를 검색한다."""

import math
import re
from collections import Counter
from pathlib import Path

from app.config import DOCUMENTS_DIR
from app.rag.chunks import Chunk, load_chunks
from app.rag.documents import COMMON


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
    def __init__(self, data_dir: Path = DOCUMENTS_DIR, k1: float = 1.5, b: float = 0.75):
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

    def search_many(
        self, queries: list[str], top_k: int = 4, targets: set[str] | None = None, exclude: list[Chunk] | None = None,
    ) -> list[tuple[Chunk, float]]:
        """여러 검색어(한국어/영어)로 검색해 순위를 합친다 (Reciprocal Rank Fusion).
        검색어마다 BM25 점수 크기가 달라 점수 대신 순위로 합치고, 반환 점수는 가장 높았던 BM25 점수다."""
        fused: dict[int, list] = {}  # id(chunk) -> [chunk, rrf 점수, 최고 BM25 점수]
        for query in queries:
            for rank, (chunk, score) in enumerate(self.search(query, top_k, targets, exclude), 1):
                entry = fused.setdefault(id(chunk), [chunk, 0.0, 0.0])
                entry[1] += 1 / (60 + rank)
                entry[2] = max(entry[2], score)
        ranked = sorted(fused.values(), key=lambda e: e[1], reverse=True)[:top_k]
        return [(chunk, score) for chunk, _, score in ranked]
