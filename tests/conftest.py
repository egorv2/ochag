"""Test environment: deterministic mock providers, no external services."""
from __future__ import annotations

import os

os.environ.update(
    {
        "APP_ENV": "test",
        "API_KEYS": "test-key",
        "DATABASE_URL": "sqlite+aiosqlite:///:memory:",
        "LLM_PROVIDERS": '[{"name":"mock","type":"mock","model":"mock-llm"}]',
        "STT_TYPE": "mock",
        "TTS_TYPE": "mock",
        "SEARCH_PROVIDER": "mock",
        "MAX_TRANSPORT": "mock",
        "EMAIL_TRANSPORT": "mock",
        "RATE_LIMIT_PER_MINUTE": "1000",
        "CONTACTS_JSON": (
            '{"мама":{"channel":"email","recipient":"mama@example.com","name":"Мама"},'
            '"ребенок":{"channel":"max","recipient":"1000000000","name":"Ребёнок"}}'
        ),
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

HEADERS = {"X-API-Key": "test-key"}


@pytest.fixture
def client() -> TestClient:
    with TestClient(app) as test_client:
        yield test_client
