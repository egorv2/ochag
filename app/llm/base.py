from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.config import Settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    delta: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    provider: str = ""


@dataclass
class LLMResult:
    text: str
    tool_calls: list[ToolCall]
    provider: str
    usage: dict[str, Any] = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[StreamChunk]: ...


class LLMChain:
    """Provider chain: first healthy provider wins."""

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("At least one LLM provider is required")
        self.providers = providers

    async def stream(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[StreamChunk]:
        errors: list[str] = []
        for provider in self.providers:
            emitted = False
            try:
                async for chunk in provider.stream(messages, tools):
                    emitted = True
                    yield chunk
                return
            except Exception as exc:  # failover только до первого токена
                errors.append(f"{provider.name}: {type(exc).__name__}: {exc}")
                if emitted:
                    raise LLMError("LLM stream failed after partial output: " + "; ".join(errors))
        raise LLMError("All LLM providers failed: " + "; ".join(errors))

    async def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> LLMResult:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        provider = ""
        usage: dict[str, Any] = {}
        async for chunk in self.stream(messages, tools):
            if chunk.delta:
                text_parts.append(chunk.delta)
            if chunk.tool_calls:
                tool_calls = chunk.tool_calls
            if chunk.provider:
                provider = chunk.provider
            if chunk.usage:
                usage = chunk.usage
        return LLMResult("".join(text_parts), tool_calls, provider, usage)


def build_llm_chain(settings: Settings) -> LLMChain:
    from app.llm.providers import MockLLM, OpenAICompatibleLLM

    providers: list[LLMProvider] = []
    for cfg in settings.llm_providers:
        if cfg.type == "mock":
            providers.append(MockLLM(name=cfg.name, mode=settings.llm_mock_mode))
        elif cfg.type == "openai":
            if not cfg.base_url:
                raise ValueError(f"LLM provider {cfg.name}: base_url is required")
            providers.append(
                OpenAICompatibleLLM(
                    name=cfg.name,
                    base_url=cfg.base_url,
                    api_key=cfg.api_key,
                    model=cfg.model,
                    timeout=cfg.timeout,
                    temperature=cfg.temperature,
                    max_tokens=cfg.max_tokens,
                )
            )
        else:
            raise ValueError(f"Unknown LLM provider type: {cfg.type}")
    return LLMChain(providers)


def tool_call_to_openai(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {
            "name": call.name,
            "arguments": json.dumps(call.arguments, ensure_ascii=False),
        },
    }
