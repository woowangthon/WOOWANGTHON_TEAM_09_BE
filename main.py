import os

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from rag import BM25Retriever

load_dotenv()

app = FastAPI()

client = OpenAI(
    api_key=os.getenv('NOVA_AI_KEY'),
    base_url="https://factchat-cloud.mindlogic.ai/v1/gateway",
)


@app.get("/")
def read_root():

    response = client.chat.completions.create(
        model="claude-sonnet-5",
        messages=[{"role": "user", "content": "Hello!"}],
    )
    print(response.choices[0].message.content)

    return {"Hello": "World"}

retriever = BM25Retriever()

SYSTEM_PROMPT = (
    "당신은 운동과 신체활동 전문 상담가입니다. "
    "아래에 주어진 [참고 문서]의 내용만을 근거로 사용자의 질문에 한국어로 답변하세요. "
    "참고 문서에 없는 내용은 추측하지 마세요. 답할 근거가 없으면 found를 false로 하고 "
    "summary에 '제공된 자료에서 해당 내용을 찾을 수 없습니다'라고 쓰세요. "
    "대상(성인, 노인, 아동·청소년 등)이 다른 내용을 섞지 말고 질문 대상에 맞는 내용만 쓰세요. "
    "마크다운 문법을 쓰지 말고 평문으로 작성하세요."
)


class Answer(BaseModel):
    found: bool = Field(description="참고 문서에서 답변 근거를 찾았는지 여부")
    summary: str = Field(description="질문에 대한 한두 문장 핵심 답변")
    details: list[str] = Field(description="세부 내용을 항목별로 나눈 문장 목록")
    cited_sources: list[int] = Field(description="답변에 사용한 [문서 N]의 번호 목록")


ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean", "description": "참고 문서에서 답변 근거를 찾았는지 여부"},
        "summary": {"type": "string", "description": "질문에 대한 한두 문장 핵심 답변"},
        "details": {
            "type": "array",
            "items": {"type": "string"},
            "description": "세부 내용을 항목별로 나눈 문장 목록",
        },
        "cited_sources": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "답변에 사용한 [문서 N]의 번호 목록",
        },
    },
    "required": ["found", "summary", "details", "cited_sources"],
    "additionalProperties": False,
}

NOT_FOUND = Answer(
    found=False,
    summary="제공된 자료에서 해당 내용을 찾을 수 없습니다.",
    details=[],
    cited_sources=[],
)


@app.get("/output")
def output(query: str = Query(..., min_length=1, description="사용자 질문"), top_k: int = 4):
    results = retriever.search(query, top_k=top_k)
    sources = [
        {"id": i, "source": chunk.source, "score": round(score, 3), "text": chunk.text}
        for i, (chunk, score) in enumerate(results, 1)
    ]
    if not results:
        return {"query": query, "answer": NOT_FOUND.model_dump(), "sources": []}

    context = "\n\n".join(f"[문서 {s['id']}] (출처: {s['source']})\n{s['text']}" for s in sources)

    response = client.chat.completions.create(
        model="claude-sonnet-5",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"[참고 문서]\n{context}\n\n[질문]\n{query}"},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "rag_answer", "strict": True, "schema": ANSWER_SCHEMA},
        },
    )

    try:
        answer = Answer.model_validate_json(response.choices[0].message.content)
    except ValidationError as e:
        raise HTTPException(status_code=502, detail=f"AI 응답 형식 오류: {e}")

    # 실제로 존재하는 문서 번호만 남긴다
    answer.cited_sources = [n for n in answer.cited_sources if 1 <= n <= len(sources)]

    return {"query": query, "answer": answer.model_dump(), "sources": sources}
