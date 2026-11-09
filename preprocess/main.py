# preprocess/src 안의 모든 PDF에서 텍스트를 추출해 preprocess/original-result/<파일명>.txt로 저장한다.
#
# 추출한 원문은 그대로 두고, 검색에 쓸 정제본(result/)은 clean.py가 만든다.

from pathlib import Path

from pypdf import PdfReader

BASE_DIR = Path(__file__).parent
SRC_DIR = BASE_DIR / "src"
RESULT_DIR = BASE_DIR / "original-result"   # 추출 원문. 정제본은 clean.py -> result/


def extract_text(pdf_path: Path) -> str:
    reader = PdfReader(pdf_path)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def main() -> None:
    RESULT_DIR.mkdir(exist_ok=True)

    pdf_files = sorted(p for p in SRC_DIR.iterdir() if p.suffix.lower() == ".pdf")
    if not pdf_files:
        print(f"PDF 파일이 없습니다: {SRC_DIR}")
        return

    for pdf_path in pdf_files:
        out_path = RESULT_DIR / f"{pdf_path.stem}.txt"
        try:
            text = extract_text(pdf_path)
        except Exception as e:
            print(f"[실패] {pdf_path.name}: {e}")
            continue
        out_path.write_text(text, encoding="utf-8")
        print(f"[완료] {pdf_path.name} -> {out_path.relative_to(BASE_DIR)} ({len(text):,}자)")


if __name__ == "__main__":
    main()
