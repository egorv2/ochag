# Голосовой AI-помощник (backend)

Бекенд для бытового голосового помощника: ответы на вопросы, поиск, отправка писем и сообщений в MAX. Python + FastAPI + Docker Compose. Все модели подключаются через OpenAI-совместимые API; есть локальные адаптеры под сервер с 4 ГБ VRAM.

Подробное проектирование → [docs/DESIGN.md](docs/DESIGN.md). Контракт API → [docs/API.md](docs/API.md).

## Быстрый старт (без ключей и GPU)

```bash
cp .env.example .env
docker compose up --build
curl http://localhost:8000/health
curl -H 'X-API-Key: dev-key' -H 'Content-Type: application/json' \
  -d '{"session_id":"phone-1","text":"Сколько варить макароны?"}' \
  http://localhost:8000/v1/chat
```

В базовом режиме работают детерминированные mock-провайдеры LLM, STT, TTS, поиска и MAX — convenience для разработки и тестов.

## Локальные модели на 4 ГБ GPU

```bash
# 1. Положите GGUF-модель в ./models/llm/
#    Qwen2.5-3B-Instruct-Q4_K_M.gguf (≈1.9 ГБ)
# 2. Положите модель Piper в ./models/piper/
#    ru_RU-irina-medium.onnx + ru_RU-irina-medium.onnx.json
# 3. Скопируйте оверрайд и задайте ключи/адреса в .env
cp .env.example .env.gpu   # при необходимости
docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile gpu up --build
```

Оверрайд добавляет `llama.cpp` (OpenAI-совместимый сервер) и `voice-gpu` (faster-whisper + Piper) и перенаправляет API на них. Внешний провайдер в `LLM_PROVIDERS` служит failover; задайте `OPENROUTER_API_KEY` в окружении. Для прода image `llama.cpp` стоит зафиксировать по build-тегу или digest.

Реальные отправки включаются в `.env`: `EMAIL_TRANSPORT=smtp` + `SMTP_*`, `MAX_TRANSPORT=bot` + `MAX_BOT_TOKEN` (или `user_bridge` + `MAX_USER_BRIDGE_URL`). По умолчанию и почта, и MAX работают в mock-режиме.

## Переключение провайдеров

Всё в `.env`, код не меняется:

- `LLM_PROVIDERS` — JSON-массив цепочки: локальный `http://llm:8080/v1`, затем OpenRouter/OpenAI и т.д.
- `STT_BASE_URL` / `TTS_BASE_URL` — либо `http://voice:8001/v1` (локальный OpenAI-совместимый gateway), либо облачный OpenAI-совместимый endpoint.
- `SEARCH_PROVIDER` — `tavily`, `brave`, `serper`, `searxng`, `mock`.
- `MAX_TRANSPORT` — `mock`, `bot` (официальный Bot API), `user_bridge` (свой companion), `disabled`.
- `SMTP_*` — SMTP с STARTTLS для почты.

## Тесты

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```

## Структура

```text
app/                 Основной API: оркестратор, инструменты, подтверждения, интеграции
services/voice_gateway/  Локальные STT/TTS с OpenAI-совместимым контрактом
docs/                Проектирование и описание API
tests/               Юнит- и интеграционные тесты
```

## Важное про MAX

Публичного user API для отправки от имени аккаунта у MAX нет; официальный Bot API требует верифицированного юрлица/ИП [1](https://dev.max.ru/docs-api), [2](https://gosmax.ru/blog/max-bot-api/). Реализован HTTP-контракт моста к вашему собственному companion-приложению (`MAX_USER_BRIDGE_URL`) и официальный бот-транспорт. Использование моста с пользовательской сессией может нарушать правила платформы — используйте бот для продакшена.

[1]: https://dev.max.ru/docs-api
[2]: https://gosmax.ru/blog/max-bot-api/
