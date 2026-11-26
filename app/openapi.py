"""OpenAPI schema enrichment: security schemes, WebSocket contract, servers."""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

WS_PATH = "/v1/ws/voice"

API_DESCRIPTION = """Бекенд голосового AI-помощника для бытовых вопросов, поиска и отправки сообщений.

Особенности:
- LLM, STT и TTS подключаются через OpenAI-совместимые API;
- поиск — через настраиваемого провайдера;
- отправка писем (SMTP) и сообщений MAX выполняется только после явного подтверждения;
- для мобильных приложений и аудиоколонки доступен потоковый WebSocket-протокол.

Все мутирующие HTTP-запросы требуют `X-API-Key`; WebSocket — `?token=`.
"""

WS_DESCRIPTION = """Потоковый голосовой диалог по WebSocket.

Клиент отправляет бинарные кадры PCM16 16 kHz mono (20–100 мс на кадр) и текстовый
кадр `{"type":"end"}` для завершения реплики. Сервер отдаёт события JSON в текстовых
кадрах, аудио-ответ — чанками base64. Авторизация: `?token=<API_KEY>`."""

WS_EXTENSION: dict[str, Any] = {
    "description": WS_DESCRIPTION,
    "parameters": [
        {
            "name": "session_id",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
            "description": "Идентификатор сессии диалога (device_id для колонки).",
        },
        {
            "name": "token",
            "in": "query",
            "required": True,
            "schema": {"type": "string"},
            "description": "API-ключ приложения.",
        },
    ],
    "x-websocket": {
        "protocol": "ws",
        "subprotocol": None,
        "client_messages": [
            {
                "type": "binary",
                "content": "PCM16 16 kHz mono, base64 не нужен (сырые байты).",
                "example": "<20-100 мс аудио>",
            },
            {
                "type": "text",
                "schema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": ["end", "cancel"]},
                        "confirm_token": {
                            "type": "string",
                            "description": "Токен из события action_draft предыдущего turn.",
                        },
                    },
                    "required": ["type"],
                },
                "example": {"type": "end", "confirm_token": "..."},
            },
        ],
        "server_events": [
            {"type": "status", "stage": "listening|transcribing|thinking|tool|speaking|cancelled"},
            {"type": "transcript", "text": "string", "final": True},
            {"type": "delta", "text": "string"},
            {
                "type": "action_draft",
                "draft": {
                    "id": "draft_...",
                    "channel": "email|max",
                    "recipient": "Мама",
                    "text": "...",
                    "confirm_token": "...",
                    "expires_at": "2026-11-25T12:00:00",
                },
            },
            {"type": "audio", "data": "<base64 audio>", "index": 0},
            {"type": "answer", "text": "string", "sources": []},
            {"type": "done", "turn_id": "turn_..."},
            {"type": "error", "message": "string"},
        ],
    },
}


def build_openapi(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=API_DESCRIPTION,
        routes=app.routes,
    )
    components = schema.setdefault("components", {})
    schemes = components.setdefault("securitySchemes", {})
    schemes["ApiKeyAuth"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
        "description": "API-ключ приложения из переменной API_KEYS.",
    }
    schemes["WebhookSecret"] = {
        "type": "apiKey",
        "in": "header",
        "name": "X-Max-Secret",
        "description": "Секрет webhook MAX из переменной MAX_WEBHOOK_SECRET.",
    }

    for path, path_item in schema.get("paths", {}).items():
        for method, operation in path_item.items():
            if not isinstance(operation, dict):
                continue
            if path.startswith("/v1/integrations/"):
                operation["security"] = [{"WebhookSecret": []}]
            elif path.startswith("/v1/"):
                operation["security"] = [{"ApiKeyAuth": []}]

    paths = schema.setdefault("paths", {})
    paths[WS_PATH] = WS_EXTENSION

    schema.setdefault("servers", [
        {"url": "http://localhost:8000", "description": "Локальный запуск / Docker Compose"},
        {"url": "https://api.example.com", "description": "Продакшен (замените на свой домен)"},
    ])
    schema.setdefault("tags", [
        {"name": "chat", "description": "Текстовые диалоги с инструментами ассистента"},
        {"name": "voice", "description": "Распознавание, синтез и потоковый голос"},
        {"name": "actions", "description": "Черновики и подтверждение отправки"},
        {"name": "integrations", "description": "Входящие webhook от MAX"},
    ])
    return schema


def install_openapi(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
