from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from app.api.deps import get_services
from app.db import get_session_factory

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["integrations"])


async def _reply_to_max(session_id: str, user_id: str, text: str) -> None:
    services = get_services()
    factory = get_session_factory()
    try:
        async with factory() as db:
            response = await services.orchestrator.handle(
                db=db, session_id=session_id, text=text
            )
        await services.dispatcher.send(
            channel="max",
            recipient=user_id,
            text=response.text,
            idempotency_key=response.turn_id,
        )
    except Exception as exc:
        logger.warning("MAX incoming message handling failed: %s", type(exc).__name__)


@router.post("/integrations/max/webhook")
async def max_webhook(
    request: Request,
    background: BackgroundTasks,
    x_max_secret: str | None = Header(default=None, alias="X-Max-Secret"),
) -> dict[str, bool]:
    settings = get_services().settings
    if x_max_secret != settings.max_webhook_secret:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    body: dict[str, Any] = await request.json()
    event_type = body.get("type") or body.get("event") or ""
    if event_type == "message_created":
        message = body.get("message") or {}
        message_body = message.get("body") or {}
        text = message_body.get("text") or message.get("text") or ""
        user = body.get("user") or {}
        user_id = str(user.get("user_id") or message.get("user_id") or "")
        chat_id = str((message.get("recipient") or {}).get("chat_id") or "")
        recipient = user_id or chat_id
        if text and recipient:
            background.add_task(_reply_to_max, f"max-{recipient}", recipient, text)
    return {"ok": True}
