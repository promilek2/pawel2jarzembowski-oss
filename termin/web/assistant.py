"""API pomocnika „Jak to działa?” (ADR-0014): `GET/POST /api/assistant`.

Treści pytań i odpowiedzi nie zapisujemy ani nie logujemy.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from slowapi import Limiter

from termin.assistant import MAX_HISTORY, MAX_QUESTION_CHARS, Assistant, Turn
from termin.config import Settings


class MessageIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS * 8)


class AssistantIn(BaseModel):
    messages: list[MessageIn] = Field(min_length=1, max_length=MAX_HISTORY * 2)
    lang: Literal["pl", "en", "uk"] = "pl"


def build_assistant_router(settings: Settings, limiter: Limiter) -> APIRouter:
    router = APIRouter(prefix="/api")

    def assistant(request: Request) -> Assistant:
        helper: Assistant | None = getattr(request.app.state, "assistant", None)
        if helper is None or not settings.ai_enabled:
            raise HTTPException(404, "Pomocnik jest wyłączony w tej instancji.")
        return helper

    @router.get("/assistant")
    @limiter.limit(settings.rate_limit_light)
    async def assistant_info(request: Request) -> dict[str, Any]:
        helper: Assistant | None = getattr(request.app.state, "assistant", None)
        enabled = helper is not None and settings.ai_enabled
        return {"enabled": enabled, "ai": bool(enabled and helper and helper.ai_available)}

    @router.post("/assistant")
    @limiter.limit(settings.ai_rate_limit)
    async def ask(request: Request, payload: AssistantIn) -> dict[str, Any]:
        helper = assistant(request)
        last = payload.messages[-1]
        if last.role != "user":
            raise HTTPException(400, "Ostatnia wiadomość musi być pytaniem.")
        if len(last.content) > MAX_QUESTION_CHARS:
            raise HTTPException(400, f"Pytanie może mieć najwyżej {MAX_QUESTION_CHARS} znaków.")
        turns = [Turn(m.role, m.content) for m in payload.messages]
        answer = await helper.answer(turns, payload.lang)
        return {"reply": answer.reply, "mode": answer.mode, "topic": answer.topic}

    return router
