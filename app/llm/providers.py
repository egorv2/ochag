from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.intents import is_cancellation, is_confirmation
from app.llm.base import StreamChunk, ToolCall

SEARCH_WORDS = ("найди", "поиск", "рецепт", "макарон", "варить", "нагар", "температур", "сколько времени", "актуальн", "цена", "курс")
SEND_WORDS = ("отправи", "напиши", "пошли", "письмо", "сообщение", "напомни")


class OpenAICompatibleLLM:
    """Any OpenAI-compatible /chat/completions endpoint, with SSE streaming."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        temperature: float = 0.3,
        max_tokens: int = 1200,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[StreamChunk]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        url = f"{self.base_url}/chat/completions"

        calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", url, json=payload, headers=headers) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if "usage" in event and event["usage"]:
                        usage = event["usage"]
                    choices = event.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    text = delta.get("content") or ""
                    if text:
                        yield StreamChunk(delta=text, provider=self.name)
                    for call in delta.get("tool_calls") or []:
                        index = call.get("index", 0)
                        acc = calls.setdefault(
                            index,
                            {"id": "", "name": "", "arguments": ""},
                        )
                        acc["id"] = call.get("id") or acc["id"]
                        fn = call.get("function") or {}
                        acc["name"] = fn.get("name") or acc["name"]
                        acc["arguments"] += fn.get("arguments") or ""

        parsed: list[ToolCall] = []
        for acc in calls.values():
            if not acc["name"]:
                continue
            try:
                arguments = json.loads(acc["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            parsed.append(
                ToolCall(
                    id=acc["id"] or f"call_{uuid.uuid4().hex[:12]}",
                    name=acc["name"],
                    arguments=arguments if isinstance(arguments, dict) else {},
                )
            )
        if parsed or usage:
            yield StreamChunk(tool_calls=parsed, usage=usage, provider=self.name)


class MockLLM:
    """Deterministic provider for local development and tests."""

    def __init__(self, name: str = "mock", mode: str = "smart") -> None:
        self.name = name
        self.mode = mode

    @staticmethod
    def _last_user_text(messages: list[dict[str, Any]]) -> str:
        for message in reversed(messages):
            if message.get("role") == "user":
                content = message.get("content")
                return content if isinstance(content, str) else json.dumps(content)
        return ""

    @staticmethod
    def _tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [m for m in messages if m.get("role") == "tool"]

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[StreamChunk]:
        text = self._last_user_text(messages)
        lowered = text.lower()

        if self.mode == "echo":
            answer = f"Вы сказали: {text}"
        elif any(result.get("name") == "confirm_draft" for result in self._tool_results(messages)):
            content = self._parse_tool_content(self._tool_results(messages), "confirm_draft")
            answer = content.get("message") or "Готово, сообщение отправлено."
        elif any(result.get("name") == "cancel_draft" for result in self._tool_results(messages)):
            answer = "Черновик отменён."
        elif any(result.get("name") == "draft_message" for result in self._tool_results(messages)):
            content = self._parse_tool_content(self._tool_results(messages), "draft_message")
            if content.get("error"):
                answer = str(content.get("message") or "Не удалось подготовить отправку.")
            else:
                answer = (
                    f"Подготовил {content.get('channel', 'сообщение')} для "
                    f"{content.get('recipient_display', content.get('recipient', ''))}: "
                    f"«{content.get('text', '')}». Подтверждаете отправку?"
                )
        elif any(result.get("name") == "web_search" for result in self._tool_results(messages)):
            content = self._parse_tool_content(self._tool_results(messages), "web_search")
            results = content.get("results") or []
            if results:
                citations = " ".join(
                    f"[{i}] {r.get('title', '')}: {r.get('snippet', '')}"
                    for i, r in enumerate(results, start=1)
                )
                answer = f"Вот что удалось найти: {citations}"
            else:
                answer = "Поиск не дал результатов."
        elif self.mode == "smart" and is_cancellation(text):
            yield StreamChunk(
                tool_calls=[ToolCall(id="mock_cancel", name="cancel_draft", arguments={})],
                provider=self.name,
            )
            return
        elif self.mode == "smart" and is_confirmation(text):
            yield StreamChunk(
                tool_calls=[
                    ToolCall(id="mock_confirm", name="confirm_draft", arguments={})
                ],
                provider=self.name,
            )
            return
        elif self.mode == "smart" and any(word in lowered for word in SEND_WORDS):
            yield StreamChunk(
                tool_calls=[
                    ToolCall(
                        id="mock_draft",
                        name="draft_message",
                        arguments={"text": self._extract_message_text(text), "recipient_hint": self._extract_recipient(text)},
                    )
                ],
                provider=self.name,
            )
            return
        elif self.mode == "smart" and any(word in lowered for word in SEARCH_WORDS):
            yield StreamChunk(
                tool_calls=[
                    ToolCall(
                        id="mock_search",
                        name="web_search",
                        arguments={"query": text},
                    )
                ],
                provider=self.name,
            )
            return
        else:
            answer = (
                "Я голосовой помощник. Могу ответить на бытовой вопрос, найти актуальную "
                "информацию и подготовить письмо или сообщение в MAX."
            )

        for part in self._split(answer):
            yield StreamChunk(delta=part, provider=self.name)
        yield StreamChunk(usage={"provider": self.name}, provider=self.name)

    @staticmethod
    def _split(text: str, size: int = 80) -> list[str]:
        return [text[i : i + size] for i in range(0, len(text), size)] or [""]

    @staticmethod
    def _parse_tool_content(results: list[dict[str, Any]], name: str) -> dict[str, Any]:
        for result in results:
            if result.get("name") != name:
                continue
            content = result.get("content", "{}")
            try:
                parsed = json.loads(content)
                return parsed if isinstance(parsed, dict) else {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    @staticmethod
    def _extract_recipient(text: str) -> str:
        for word in ("мам", "ребен", "ребён", "пап", "жен", "муж", "друг"):
            if word in text.lower():
                return word
        return ""

    @staticmethod
    def _extract_message_text(text: str) -> str:
        for marker in ("что ", "текст: ", "сообщение: "):
            if marker in text.lower():
                return text.split(marker, 1)[1].strip()
        return "Я задерживаюсь, буду через 20 минут."
