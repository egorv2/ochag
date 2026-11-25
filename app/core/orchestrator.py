from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.core.confirmations import ConfirmationService
from app.core.intents import is_cancellation, is_confirmation
from app.core.tools import TOOL_SCHEMAS, ToolContext, dumps, execute_tool
from app.db import SessionRow, TurnRow, get_recent_turns, utcnow
from app.integrations.contacts import ContactBook
from app.llm import LLMChain, tool_call_to_openai
from app.schemas import ChatResponse
from app.search.providers import SearchProvider

EventCallback = Callable[[dict[str, Any]], Awaitable[None] | None]

SYSTEM_PROMPT = """Ты — голосовой помощник по бытовым вопросам. Отвечай коротко, по-русски, дружелюбно.
Правила:
1. Для актуальных или конкретных фактов (рецепты, время приготовления, цены, инструкции) вызови web_search и опирайся на результаты, указывая [1], [2].
2. Просьбу отправить письмо/сообщение выполни в два шага: сначала draft_message, покажи текст и спроси подтверждения. Никогда не говори «отправлено», пока не будет вызвана confirm_draft и не придёт результат.
3. confirm_draft вызывай только после явного подтверждения пользователя («да», «подтверждаю», «отправь»). Если пользователь ещё не подтверждал — только подготовь черновик.
4. Получателей бери только из списка контактов. Не выдумывай адреса и ID.
5. Если инструмент вернул error, честно скажи, что не получилось, и предложи альтернативу.
6. Не используй Markdown-разметку в ответе — ответ будет озвучен.
7. Никогда не произноси и не показывай пользователю токены подтверждения — они нужны только клиентскому приложению.

Доступные контакты:
{contacts}
"""


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        llm: LLMChain,
        contacts: ContactBook,
        search: SearchProvider,
        confirmations: ConfirmationService,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.contacts = contacts
        self.search = search
        self.confirmations = confirmations

    @staticmethod
    async def _emit(event: EventCallback | None, payload: dict[str, Any]) -> None:
        if event is None:
            return
        result = event(payload)
        if inspect.isawaitable(result):
            await result

    async def handle(
        self,
        db: AsyncSession,
        session_id: str,
        text: str,
        locale: str = "ru-RU",
        confirm_token: str | None = None,
        on_event: EventCallback | None = None,
    ) -> ChatResponse:
        session = await db.get(SessionRow, session_id)
        if session is None:
            db.add(SessionRow(id=session_id, locale=locale))
        else:
            session.updated_at = utcnow()

        recent = await get_recent_turns(db, session_id)
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT.format(
                    contacts=json.dumps(self.contacts.as_tool_context(), ensure_ascii=False)
                ),
            }
        ]
        for turn in recent:
            if turn.user_text:
                messages.append({"role": "user", "content": turn.user_text})
            if turn.assistant_text:
                messages.append({"role": "assistant", "content": turn.assistant_text})
        messages.append({"role": "user", "content": text})

        ctx = ToolContext(
            db=db,
            session_id=session_id,
            user_text=text,
            user_confirmed=is_confirmation(text),
            confirm_token=confirm_token,
            contacts=self.contacts,
            search=self.search,
            confirmations=self.confirmations,
        )
        await self._emit(on_event, {"type": "status", "stage": "thinking"})

        parts: list[str] = []
        tool_log: list[dict[str, Any]] = []
        provider = ""
        usage: dict[str, Any] = {}

        for _round in range(self.settings.llm_max_tool_rounds):
            round_parts: list[str] = []
            calls: list[Any] = []
            async for chunk in self.llm.stream(messages, TOOL_SCHEMAS):
                if chunk.delta:
                    round_parts.append(chunk.delta)
                    await self._emit(on_event, {"type": "delta", "text": chunk.delta})
                if chunk.tool_calls:
                    calls = chunk.tool_calls
                if chunk.provider:
                    provider = chunk.provider
                if chunk.usage:
                    usage = chunk.usage
            parts = round_parts

            if not calls:
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": "".join(round_parts),
                    "tool_calls": [tool_call_to_openai(call) for call in calls],
                }
            )
            for call in calls:
                await self._emit(on_event, {"type": "status", "stage": "tool", "tool": call.name})
                result = await execute_tool(call.name, call.arguments, ctx)
                tool_log.append(
                    {"name": call.name, "arguments": call.arguments, "result": result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": dumps(result),
                    }
                )
                if call.name == "draft_message" and ctx.draft:
                    await self._emit(
                        on_event,
                        {"type": "action_draft", "draft": ctx.draft.model_dump(mode="json")},
                    )
        else:
            # Лимит раундов исчерпан: честно уточняем, чтобы не обещать ложное действие.
            if not ctx.action_result:
                parts.append("Я остановился, чтобы не сделать лишнего. Уточните, пожалуйста, запрос.")

        # Детерминированная страховка для слабых локальных моделей.
        if ctx.action_result is None and ctx.draft is None and is_cancellation(text):
            await execute_tool("cancel_draft", {}, ctx)
        elif ctx.action_result is None and ctx.draft is None and is_confirmation(text):
            pending = await self.confirmations.latest_pending(db, session_id)
            if pending is not None:
                await execute_tool(
                    "confirm_draft", {"draft_id": pending.id}, ctx
                )

        answer = "".join(parts).strip()
        if not answer:
            if ctx.action_result:
                answer = ctx.action_result.message
            elif ctx.draft:
                answer = (
                    f"Подготовил {ctx.draft.channel} для {ctx.draft.recipient_display or ctx.draft.recipient}: "
                    f"«{ctx.draft.text}». Подтверждаете отправку?"
                )
            elif ctx.sources:
                answer = "Вот что удалось найти: " + " ".join(
                    f"[{i}] {s.snippet}" for i, s in enumerate(ctx.sources, 1)
                )
            else:
                answer = "Чем могу помочь?"

        turn_id = f"turn_{uuid.uuid4().hex[:16]}"
        db.add(
            TurnRow(
                id=turn_id,
                session_id=session_id,
                user_text=text,
                assistant_text=answer,
                sources=[s.as_dict() if hasattr(s, "as_dict") else s.model_dump() for s in ctx.sources],
                tool_calls=tool_log,
                provider=provider,
                created_at=utcnow(),
            )
        )
        await db.commit()

        response = ChatResponse(
            session_id=session_id,
            turn_id=turn_id,
            text=answer,
            sources=ctx.sources,
            draft=ctx.draft,
            action_result=ctx.action_result,
            provider=provider,
            usage=usage,
        )
        await self._emit(
            on_event,
            {"type": "answer", "text": answer, "sources": [s.model_dump() for s in ctx.sources]},
        )
        await self._emit(on_event, {"type": "done", "turn_id": turn_id})
        return response
