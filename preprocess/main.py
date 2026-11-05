from pypdf import PdfReader

# 1. PDF 파일 열기 (이진 읽기 모드 'rb')
reader = PdfReader("신체활동 지침서.pdf")

# 2. 전체 페이지 수 확인
print(f"전체 페이지 수: {len(reader.pages)}")

# 3. 특정 페이지(예: 첫 번째 페이지, 인덱스는 0부터 시작) 가져오기
full_text = ""
for page in reader.pages:
  full_text += page.extract_text() + "\n"

print(full_text)

reader.close()

f = open('./신체활동 지침서.txt', 'w')

f.write(full_text)
f.flush()

f.close()