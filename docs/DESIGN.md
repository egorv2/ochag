# Проектирование бекенда голосового AI-помощника

Версия: 1.0 · Дата: 2026-11-25

## 1. Контекст и ограничения

- Основной язык ответов — русский.
- Типовые задачи: бытовые вопросы (рецепты, время варки, удаление нагара), поиск актуальной информации, отправка писем и сообщений в MAX.
- Железо: сервер с видеокартой **4 ГБ VRAM**.
- Клиенты: сначала мобильное приложение, позже аудиоколонка. Бекенд не должен знаний о конкретном клиенте — только открытый контракт.
- Стек: Python, Docker Compose, OpenAI-совместимые API для всех моделей.

## 2. Решения, зафиксированные по ответам на вопросы

| Вопрос | Решение | Следствие в архитектуре |
|---|---|---|
| STT/TTS | Гибридно, переключаемо | Локальный voice-gateway реализует **OpenAI-совместимый** контракт; облако подключается сменой `base_url` |
| LLM | Локальная модель + внешний failover | Цепочка провайдеров `LLM_PROVIDERS`, local → cloud, единый OpenAI-совместимый клиент |
| Транспорт | REST + WebSocket | REST для turns/файлов, WS для живого диалога и потокового TTS |
| Поиск | Провайдер в конфиге | `SEARCH_PROVIDER`: tavily / brave / serper / searxng / mock |
| MAX | От имени пользователя | `MaxUserBridgeTransport` + легальный `MaxBotTransport` как fallback; **не** встраиваем неофициальный протокол в ядро |
| Подтверждение | Всегда | Двухфазная модель: `draft` → явное `confirm` → отправка; одноразовый токен, TTL, аудит |

## 3. Высокоуровневая архитектура

```text
Мобильное приложение / колонка
   │  REST /v1/chat, /v1/voice   WS /v1/ws/voice
   ▼
┌──────────────────── API (FastAPI) ────────────────────┐
│ auth → rate limit → idempotency → Orchestrator        │
│  ├─ LLM chain (OpenAI-compatible: local → cloud)      │
│  ├─ Tools: web_search, draft_message, confirm_draft…  │
│  ├─ ConfirmationService (SQLite, TTL, one-time token) │
│  ├─ MessageDispatcher → SMTP / MAX bridge / MAX bot   │
│  └─ AudioClient → STT/TTS (OpenAI-compatible contract)│
└────────────────────────────────────────────────────────┘
        │                    │                  │
        ▼                    ▼                  ▼
 voice-gateway (GPU*)   llama.cpp (GPU*)   SearXNG / поиск API
 faster-whisper + Piper  Qwen2.5-3B Q4      Tavily/Brave/Serper
 * только в профиле gpu
```

Ключевой принцип: **все три модели говорят на одном OpenAI-совместимом протоколе**. Переход с локальной модели на облачную и обратно — только конфигурация.

## 4. Распределение 4 ГБ VRAM

| Компонент | Размер | WHERE |
|---|---|---|
| Qwen2.5-3B-Instruct Q4_K_M, ctx 4096, KV в q8_0 | ~2.4–2.9 ГБ | llama.cpp server |
| faster-whisper small, `int8_float16` | ~0.5–0.8 ГБ | voice-gateway |
| Piper ru_RU-irina-medium | 0 ГБ (CPU, ONNX) | voice-gateway |
| Резерв под буферы/фрагментацию | ~0.3–0.6 ГБ | — |

Меры:
1. Явный `--n-gpu-layers` и `--ctx-size 4096` (не 16k — KV cache съест весь бюджет).
2. STT загружается лениво и выгружается после `STT_IDLE_UNLOAD_SECONDS` (по умолчанию 120 с).
3. Piper на CPU — не занимает VRAM.
4. При нехватке VRAM достаточно убрать локальный LLM из цепочки (оставить внешний) — код не меняется.
5. Порядок деградации в цепочке LLM: local timeout/error → следующий провайдер. При partially streamed ответе failover не делается (чтобы не склеивать два ответа) — ошибка отдаётся клиенту как событие `error`.

## 5. Диалоговый оркестратор

Шаг за turn:
1. Загрузить последние N реплик сессии (по умолчанию 8) из SQLite.
2. System prompt: кратко, по-русски, не выдумывать факты, для свежей информации звать `web_search`, для отправки — только `draft_message`, отправку выполняет только `confirm_draft`.
3. Вызвать LLM с инструментами. Если пришли tool_calls — выполнить, положить результаты обратно в историю, повторить (максимум 3 итерации).
4. Детерминированные страховки для слабых локальных моделей:
   - явное «да / подтверждаю / отправь» + есть ожидающий черновик → `confirm_draft` даже если LLM не вызвал инструмент;
   - «отмени» → `cancel_draft`;
   - LLM не может подтвердить черновик внутри того же turn без явного подтверждения в тексте пользователя.
5. Сохранить turn, источники, вызовы инструментов и аудит в БД.

Инструменты: `web_search`, `draft_message`, `confirm_draft`, `cancel_draft`, `list_contacts`, `get_current_time`.

### Двухфазная отправка (обязательное подтверждение)

```text
пользователь: «напиши маме что я задерживаюсь»
  → draft_message(channel=email, recipient=мама, text=...)
  → БД: draft(status=pending, token_hash=sha256(token), expires_at=now+10min)
  → ответ: «Готово письмо маме: «…». Подтверждаете отправку?»
пользователь: «да, отправь»
  → confirm_draft(token=...) → CONDITIONAL UPDATE pending→sending
  → MessageDispatcher.send(...) → status=sent / failed, external_id
  → аудит: кто, что, кому, когда, результат
```

- Получатель резолвится **только** через allowlist `CONTACTS_JSON`. Модель не может придумать адрес/ID.
- Токен подтверждения одноразовый, хранится только хэшем, привязан к сессии.
- `Idempotency-Key` при отправке предотвращает повторную отправку при ретрае запроса.
- Политика MAX: не более ~2 сообщений в секунду в чат — исходящие идут через локальный rate limiter с `Retry-After`.

## 6. Поиск

Единый интерфейс `SearchProvider.search(query, limit) -> list[SearchResult]`. Провайдеры: Tavily, Brave, Serper, SearXNG (self-hosted), mock. Результаты попадают в prompt как нумерованные источники; LLM обязан ссылаться `[1]`, `[2]`. В answer возвращаются `sources` для отображения в приложении.

## 7. MAX: важное уточнение

Официальный MAX Bot API — REST на `https://platform-api2.max.ru`, токен в заголовке `Authorization: <access_token>`, отправка `POST /messages?user_id=…` или `?chat_id=…`, события через webhook/long polling [3](https://gosmax.ru/blog/max-bot-api/), [10](https://www.securitylab.ru/blog/personal/Bitshield/362495.php). Публичного user API для отправки «от имени аккаунта» нет; боты создаются только верифицированными юрлицами/ИП/самозанятыми и проходят модерацию [2](https://vtemah.livejournal.com/2896881.html), [7](https://www.up-im.ru/blog/kak-sozdat-bota-v-max/).

Поэтому в коде:
- `MaxUserBridgeTransport` — **интерфейс HTTP-моста** к собственному companion-приложению, которое хранит сессию пользователя и реально отправляет сообщение. Бекенд не содержит неофициальных протоколов и не обходит защиту MAX.
- `MaxBotTransport` — официальный Bot API, включая приём `message_created` webhook.
- `mock` — для разработки без токенов.

Риск: мост на базе пользовательской сессии может нарушать правила платформы и привести к блокировке; для продакшена рекомендуется бот. Это осознанный компромисс, выбранный пользователем.

## 8. Модель данных (SQLite, SQLAlchemy 2 async)

- `sessions` — id, user_id, locale, timestamps.
- `turns` — id, session_id, user_text, assistant_text, sources, tool_calls, created_at.
- `drafts` — id, session_id, channel, recipient, text, subject, status (`pending|sending|sent|failed|cancelled|expired`), token_hash, expires_at, external_id, attempts.
- `audit_log` — action, status, detail, created_at.
- `idempotency` — key, endpoint, response, created_at.

## 9. HTTP-контракт

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/health`, `/ready` | проверки |
| POST | `/v1/chat` | текстовый turn → ответ + источники + черновик |
| POST | `/v1/stt` | аудиофайл → текст |
| POST | `/v1/tts` | текст → аудио (wav/mp3/opus) |
| POST | `/v1/voice` | аудиофайл → аудио-ответ (заголовки с текстом) |
| WS | `/v1/ws/voice` | потоковый диалог: `audio_chunk`, `end`, события `transcript`, `delta`, `audio`, `action_draft`, `done`, `error` |
| GET | `/v1/actions/drafts` | черновики сессии |
| POST | `/v1/actions/{id}/confirm` | подтвердить отправку |
| POST | `/v1/actions/{id}/cancel` | отменить черновик |
| POST | `/v1/integrations/max/webhook` | входящие события MAX |

Все мутирующие запросы требуют `X-API-Key`; WS — `?token=`. Подробности в [API.md](API.md).

## 10. Безопасность и эксплуатация

- Секреты только в env/.env, не в логах; `LOG_PII=false` по умолчанию (текст пользователя не пишется в логи).
- Webhook MAX защищён секретом в заголовке.
- Простая аутентификация API-ключами и in-memory rate limiter; для нескольких инстансов — Redis/PostgreSQL и общая БД (точка роста описана ниже).
- Таймауты на все внешние вызовы, retry с jitter только для идемпотентных операций.
- Метрики: количество turns, доля fallback LLM, latency STT/LLM/TTS, доля неудачных отправок.

## 11. Эволюция: мобильное приложение и колонка

- **Мобильное**: REST + WS, загрузка аудио в `multipart/form-data`, получение mp3/opus; `session_id` хранится на клиенте; кнопки подтверждения используют REST endpoints.
- **Колонка**: тот же WS с `audio_chunk` (PCM16 16 kHz mono, base64) и `wake_word` на устройстве; бекенд не знает о wake word. Требуется `device_id` вместо `session_id` — реализовано как произвольный `session_id`.
- **Масштабирование**: заменить SQLite на PostgreSQL, in-memory rate limiter на Redis, добавить очередь отправок (Celery/RQ) и хранилище аудио (S3) — контракт API не меняется.

## 12. Что остаётся за рамками первой версии

- Wake word и VAD на устройстве (это клиент).
- Реальная аутентификация пользователей (сейчас API-ключ приложения + session_id; для прода — OAuth/JWT).
- Тонкая настройка приватности/удаления истории по GDPR/152-ФЗ.
- Реальный bridge для MAX разворачивается отдельно; в репозитории только его HTTP-контракт.

## Источники

- [Официальная документация MAX Bot API](https://dev.max.ru/docs-api)
- [ГосMax: API MAX для ботов, 2026](https://gosmax.ru/blog/max-bot-api/)
- [Обзор ограничений MAX и ботов](https://vtemah.livejournal.com/2896881.html)
- [Модель Qwen2.5-3B GGUF (размеры квантов)](https://huggingface.co/bartowski/Qwen2.5-3B-Instruct-GGUF)
