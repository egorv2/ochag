from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.confirmations import ConfirmationService
from app.integrations.contacts import ContactBook
from app.schemas import ActionResult, DraftItem, SourceItem
from app.search.providers import SearchProvider

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Искать актуальную информацию в интернете. Используй для свежих фактов, рецептов, цен, инструкций.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Поисковый запрос"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_message",
            "description": "Подготовить черновик письма или сообщения. НЕ отправляет. Отправка возможна только после явного подтверждения пользователя.",
            "parameters": {
                "type": "object",
                "properties": {
                    "recipient_hint": {
                        "type": "string",
                        "description": "Имя или псевдоним получателя из списка контактов",
                    },
                    "channel": {
                        "type": "string",
                        "enum": ["email", "max", "auto"],
                        "description": "Канал: email, max или auto (по контакту)",
                    },
                    "text": {"type": "string", "description": "Текст сообщения"},
                    "subject": {"type": "string", "description": "Тема письма (только для email)"},
                },
                "required": ["recipient_hint", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_draft",
            "description": "Отправить ранее подготовленный черновик. Вызывай ТОЛЬКО когда пользователь явно подтвердил отправку словами «да», «подтверждаю», «отправь».",
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_id": {"type": "string", "description": "ID черновика, если известен"},
                    "confirm_token": {
                        "type": "string",
                        "description": "Одноразовый токен из ответа draft_message, если передан клиентом",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_draft",
            "description": "Отменить ожидающий черновик по просьбе пользователя.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_contacts",
            "description": "Показать доступных получателей (только из whitelist).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Узнать текущие дату и время в UTC.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]


@dataclass
class ToolContext:
    db: AsyncSession
    session_id: str
    user_text: str
    user_confirmed: bool
    confirm_token: str | None
    contacts: ContactBook
    search: SearchProvider
    confirmations: ConfirmationService
    sources: list[SourceItem] = field(default_factory=list)
    draft: DraftItem | None = None
    action_result: ActionResult | None = None
    draft_token: str | None = None
    draft_id: str | None = None


async def execute_tool(name: str, arguments: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """Execute one tool call and return a JSON-serializable result for the LLM."""
    try:
        if name == "web_search":
            query = str(arguments.get("query") or ctx.user_text)
            results = await ctx.search.search(query, limit=4)
            ctx.sources = [SourceItem(**r.as_dict()) for r in results]
            return {
                "results": [r.as_dict() for r in results],
                "message": f"Найдено результатов: {len(results)}",
            }

        if name == "draft_message":
            hint = str(arguments.get("recipient_hint") or arguments.get("recipient") or "")
            channel = str(arguments.get("channel") or "auto").lower()
            contact = ctx.contacts.resolve(hint, None if channel == "auto" else channel)
            if contact is None:
                return {
                    "error": "Получатель не найден в списке контактов",
                    "available": ctx.contacts.as_tool_context(),
                    "message": "Уточните имя получателя из списка контактов.",
                }
            text = str(arguments.get("text") or "").strip()
            if not text:
                return {"error": "Пустой текст сообщения", "message": "Не удалось подготовить сообщение."}
            draft = await ctx.confirmations.create_draft(
                db=ctx.db,
                session_id=ctx.session_id,
                channel=contact.channel,
                recipient=contact.recipient,
                recipient_display=contact.name,
                text=text,
                subject=str(arguments.get("subject") or ""),
            )
            ctx.draft = draft
            ctx.draft_token = draft.confirm_token
            ctx.draft_id = draft.id
            return {
                "draft_id": draft.id,
                "confirm_token": draft.confirm_token,
                "recipient": contact.name,
                "channel": contact.channel,
                "text": text,
                "expires_at": draft.expires_at.isoformat() if draft.expires_at else None,
                "message": "Черновик создан. Нужно явное подтверждение пользователя.",
            }

        if name == "confirm_draft":
            if not ctx.user_confirmed and not ctx.confirm_token:
                return {
                    "error": "Explicit confirmation required",
                    "message": "Отправка запрещена без явного подтверждения пользователя.",
                }
            draft_id = str(arguments.get("draft_id") or ctx.draft_id or "")
            token = str(arguments.get("confirm_token") or ctx.confirm_token or "")
            if not draft_id:
                pending = await ctx.confirmations.latest_pending(ctx.db, ctx.session_id)
                if pending is None:
                    return {"error": "No pending draft", "message": "Нет черновиков, ожидающих подтверждения."}
                draft_id = pending.id
            if not token and ctx.draft_token and draft_id == ctx.draft_id:
                token = ctx.draft_token
            if not token and not ctx.user_confirmed:
                return {
                    "error": "Confirmation token required",
                    "message": "Клиент должен передать токен подтверждения из ответа черновика.",
                }
            # Голосовой сценарий: явное «да, отправь» + активная сессия заменяют токен.
            # REST-эндпоинт подтверждения токен требует всегда.
            result = await ctx.confirmations.confirm(
                db=ctx.db,
                draft_id=draft_id,
                confirm_token=token,
                session_id=ctx.session_id,
                skip_token_check=not token,
            )
            ctx.action_result = result
            return {"result": result.model_dump(), "message": result.message}

        if name == "cancel_draft":
            draft_id = str(arguments.get("draft_id") or ctx.draft_id or "")
            if not draft_id:
                pending = await ctx.confirmations.latest_pending(ctx.db, ctx.session_id)
                if pending is None:
                    return {"error": "No pending draft", "message": "Нечего отменять."}
                draft_id = pending.id
            result = await ctx.confirmations.cancel(ctx.db, draft_id, ctx.session_id)
            ctx.action_result = result
            return {"result": result.model_dump(), "message": result.message}

        if name == "list_contacts":
            return {"contacts": ctx.contacts.as_tool_context()}

        if name == "get_current_time":
            return {"now": datetime.now(timezone.utc).isoformat()}

        return {"error": f"Unknown tool: {name}"}
    except Exception as exc:
        # HTTPException от ConfirmationService должен дойти до API-слоя:
        if exc.__class__.__name__ == "HTTPException":
            raise
        return {"error": f"{type(exc).__name__}: {exc}", "message": "Инструмент не смог выполниться."}


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
