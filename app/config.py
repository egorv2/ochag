from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderSettings(BaseModel):
    name: str
    type: str = "openai"  # openai | mock
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = 30.0
    temperature: float = 0.3
    max_tokens: int = 1200


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    app_env: str = "dev"
    log_level: str = "INFO"
    log_pii: bool = False
    api_keys: str = "dev-key"
    database_url: str = "sqlite+aiosqlite:///./data/assistant.db"
    cors_origins: str = "*"

    llm_providers: list[LLMProviderSettings] = [
        LLMProviderSettings(name="mock", type="mock", model="mock-llm")
    ]
    llm_mock_mode: str = "smart"
    llm_max_tool_rounds: int = 3

    stt_type: str = "openai"  # openai | mock
    stt_base_url: str = "http://voice:8001/v1"
    stt_api_key: str = "local"
    stt_model: str = "small"
    stt_language: str = "ru"
    stt_timeout: float = 60.0
    stt_mock_text: str = "Сколько по времени варить макароны?"
    tts_type: str = "openai"  # openai | mock
    tts_base_url: str = "http://voice:8001/v1"
    tts_api_key: str = "local"
    tts_model: str = "ru_RU-irina-medium"
    tts_voice: str = "ru_RU-irina-medium"
    tts_format: str = "mp3"
    tts_timeout: float = 60.0

    search_provider: str = "mock"
    search_api_key: str = ""
    search_base_url: str = "http://searxng:8080"
    search_timeout: float = 15.0
    search_max_results: int = 4

    max_transport: str = "mock"
    max_api_base: str = "https://platform-api2.max.ru"
    max_bot_token: str = ""
    max_webhook_secret: str = "change-me"
    max_bot_rate_limit_per_sec: float = 2.0
    max_user_bridge_url: str = "http://max-bridge:9000"
    max_user_bridge_token: str = ""

    email_transport: str = "smtp"  # smtp | mock
    smtp_host: str = "smtp.example.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "assistant@example.com"
    smtp_use_tls: bool = True
    smtp_timeout: float = 15.0

    contacts_json: str = "{}"
    require_contact_allowlist: bool = True
    confirmation_ttl_seconds: int = 600
    rate_limit_per_minute: int = 30
    max_audio_bytes: int = 25_000_000

    @field_validator("llm_providers", mode="before")
    @classmethod
    def parse_llm_providers(cls, value: Any) -> Any:
        if isinstance(value, str):
            parsed = json.loads(value)
            if not isinstance(parsed, list) or not parsed:
                raise ValueError("LLM_PROVIDERS must be a non-empty JSON array")
            return parsed
        return value

    @field_validator("contacts_json")
    @classmethod
    def validate_contacts(cls, value: str) -> str:
        parsed = json.loads(value or "{}")
        if not isinstance(parsed, dict):
            raise ValueError("CONTACTS_JSON must be a JSON object")
        return value

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
