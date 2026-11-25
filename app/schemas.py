from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20_000)
    locale: str = "ru-RU"
    confirm_token: str | None = None


class SourceItem(BaseModel):
    title: str
    url: str
    snippet: str = ""


class DraftItem(BaseModel):
    id: str
    channel: str
    recipient: str
    recipient_display: str
    subject: str = ""
    text: str
    status: str
    confirm_token: str | None = None
    expires_at: datetime | None = None


class ActionResult(BaseModel):
    draft_id: str
    status: str
    channel: str = ""
    recipient: str = ""
    external_id: str = ""
    message: str = ""


class ChatResponse(BaseModel):
    session_id: str
    turn_id: str
    text: str
    sources: list[SourceItem] = []
    draft: DraftItem | None = None
    action_result: ActionResult | None = None
    provider: str = ""
    usage: dict[str, Any] = {}


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    format: str = "mp3"
    voice: str | None = None
    model: str | None = None


class ConfirmRequest(BaseModel):
    confirm_token: str
