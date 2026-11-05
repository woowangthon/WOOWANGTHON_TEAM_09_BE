import os

from dotenv import load_dotenv
from fastapi import FastAPI
from openai import OpenAI

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