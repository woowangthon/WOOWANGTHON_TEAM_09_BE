# 참고 문서 검색(RAG).
#
# - documents.py: 문서를 섹션으로 나누고 섹션마다 대상(성인, 노인, 임산부 등)을 붙인다.
# - chunks.py: 섹션을 조각(chunk)으로 나눈다.
# - retriever.py: 사용자 대상 + "공통" 조각 중에서 BM25로 검색한다.

from app.rag.chunks import Chunk
from app.rag.retriever import BM25Retriever

__all__ = ["BM25Retriever", "Chunk"]
