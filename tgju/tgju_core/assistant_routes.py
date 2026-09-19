# -*- coding: utf-8 -*-
"""Authenticated API routes for the independent Telegram AI assistant."""
import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from tgju_core import auth

router = APIRouter(dependencies=[Depends(auth.require_auth)])


@router.get("/api/assistant")
def get_assistant_config():
    from tgju_engine_ai import load_ai_config
    from tgju_engine_ai_assistant import (
        ASSISTANT_RUNTIME, load_assistant_config, public_assistant_config)

    public = public_assistant_config(load_assistant_config())
    public["runtime"] = dict(ASSISTANT_RUNTIME)
    providers = []
    for name, provider in (load_ai_config().get("providers") or {}).items():
        if provider.get("kind") == "mock":
            continue
        providers.append({
            "id": name,
            "label": provider.get("label") or name,
            "model": provider.get("model") or "",
            "enabled": bool(provider.get("enabled", True)),
        })
    public["providers"] = providers
    return public


@router.put("/api/assistant")
async def put_assistant_config(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": "bad body"}, status_code=400)

    from tgju_engine_ai_assistant import save_assistant_config, public_assistant_config

    config = save_assistant_config(body)
    return {"ok": True, "config": public_assistant_config(config)}


@router.post("/api/assistant/test")
async def test_assistant_connection(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    from tgju_engine_ai_assistant import load_assistant_config, test_assistant_token

    token = str(body.get("token") or load_assistant_config().get("token") or "").strip()
    if not token:
        return JSONResponse({"ok": False, "error": "token required"}, status_code=400)
    result = await asyncio.to_thread(test_assistant_token, token)
    return result if result.get("ok") else JSONResponse(result, status_code=400)


@router.post("/api/assistant/models")
async def assistant_models(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    from tgju_engine_ai_assistant import list_assistant_models

    overrides = {
        "base_url": str(body.get("base_url") or "").strip(),
        "api_key": str(body.get("api_key") or "").strip(),
    }
    result = await asyncio.to_thread(list_assistant_models, overrides)
    return result if result.get("ok") else JSONResponse(result, status_code=400)


@router.post("/api/assistant/ai/test")
async def test_assistant_ai_provider(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    from tgju_engine_ai_assistant import test_assistant_provider

    overrides = {
        "base_url": str(body.get("base_url") or "").strip(),
        "api_key": str(body.get("api_key") or "").strip(),
        "model": str(body.get("model") or "").strip(),
    }
    result = await asyncio.to_thread(test_assistant_provider, overrides)
    return result if result.get("ok") else JSONResponse(result, status_code=400)


@router.post("/api/assistant/simulate")
async def simulate_assistant(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = str(body.get("question") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "question is required"}, status_code=400)

    from tgju_engine_ai_assistant import answer_question

    result = await asyncio.to_thread(answer_question, question)
    if result.get("ok"):
        return result
    status = 400 if result.get("error") in {"question is required", "question is too long"} else 503
    return JSONResponse(result, status_code=status)


@router.post("/api/assistant/stream")
async def stream_assistant(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    question = str(body.get("question") or "").strip()
    if not question:
        return JSONResponse({"ok": False, "error": "question is required"}, status_code=400)

    from fastapi.responses import StreamingResponse
    from tgju_engine_ai_assistant import answer_question_stream
    import json

    async def event_generator():
        for chunk in answer_question_stream(question):
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.002)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

