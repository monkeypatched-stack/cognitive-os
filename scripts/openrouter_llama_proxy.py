#!/usr/bin/env python3
"""Minimal OpenAI-compatible shim so scripts/mission_chatbot.py can run
against OpenRouter instead of a local llama-server.

mission_chatbot.py posts unauthenticated requests to LLAMA_URL (its own
default: http://localhost:8090/v1/chat/completions), matching a local
llama.cpp server. No chat-capable GGUF model is available on this machine
(only LM Studio's bundled nomic-embed-text embedding model) to actually run
one, but OPENROUTER_API_KEY is already set in .env, matching this project's
OpenRouter-primary provider strategy. Rather than edit mission_chatbot.py to
add an Authorization header (this repo's own convention: new files, not
edits, for a one-off local-dev need), this listens on the same
127.0.0.1:8090 default and forwards to OpenRouter with the key attached --
mission_chatbot.py needs zero changes and zero awareness this exists.

Usage:
    python3 scripts/openrouter_llama_proxy.py
    # then, in another terminal:
    MISSION_FLEET="drone-a=http://127.0.0.1:9010,drone-b=http://127.0.0.1:9011,drone-c=http://127.0.0.1:9012" \\
    LLAMA_MODEL=openai/gpt-4o-mini \\
        .venv/bin/python3 scripts/mission_chatbot.py
"""

from __future__ import annotations

import os

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = os.environ["OPENROUTER_API_KEY"]
# mission_chatbot.py's own LLAMA_MODEL default ("gemma-3-4b-it-Q4_K_M") is a
# local GGUF filename OpenRouter has never heard of -- swap in a real
# OpenRouter model id unless the caller already set one.
DEFAULT_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
LOCAL_MODEL_PLACEHOLDER = "gemma-3-4b-it-Q4_K_M"

app = FastAPI()


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    if not body.get("model") or body["model"] == LOCAL_MODEL_PLACEHOLDER:
        body["model"] = DEFAULT_MODEL
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

    if not body.get("stream"):
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(OPENROUTER_URL, json=body, headers=headers)
        return JSONResponse(content=resp.json(), status_code=resp.status_code)

    async def relay():
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", OPENROUTER_URL, json=body, headers=headers) as resp:
                async for chunk in resp.aiter_raw():
                    yield chunk

    return StreamingResponse(relay(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("LLAMA_PROXY_PORT", "8090")))
