"""섹션을 검색 단위인 조각(chunk)으로 나누고, 모든 참고 문서의 조각을 읽어 온다."""

from dataclasses import dataclass
from pathlib import Path

from app.config import DOCUMENTS_DIR
from app.rag.documents import COMMON, SPLITTERS, split_default

CHUNK_SIZE = 800     # 청크 최대 글자 수 (AI에게 보내는 토큰을 줄이려고 작게. 성인 핵심 지침 676자는 한 청크에 들어감)
CHUNK_OVERLAP = 150  # 섹션이 길어 여러 청크로 나눌 때 겹치는 글자 수


@dataclass
class Chunk:
    source: str
    targets: tuple[str, ...]  # 이 청크가 해당하는 대상. 일반 내용은 ("공통",)
    text: str
    core: bool = False        # 해당 대상의 핵심 신체활동 지침(권장량)이 담긴 청크


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


def load_chunks(data_dir: Path = DOCUMENTS_DIR) -> list[Chunk]:
    chunks = []
    for path in sorted(data_dir.glob("*.txt")):
        splitter = SPLITTERS.get(path.stem, split_default)
        for section in splitter(path.read_text(encoding="utf-8")):
            for text in _split_chunks(section.lines):
                chunks.append(Chunk(path.stem, section.targets, text, _is_core(path.stem, section.targets, text)))
    return chunks
