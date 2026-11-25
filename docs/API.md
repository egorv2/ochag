# API бекенда (v1)

Базовый URL: `http://localhost:8000`. Аутентификация: заголовок `X-API-Key: <ключ из API_KEYS>`; для WebSocket — `?token=<ключ>`.

## POST /v1/chat

```json
{
  "session_id": "phone-1",
  "text": "Напиши маме, что я задерживаюсь на ужин",
  "locale": "ru-RU",
  "confirm_token": null
}
```

Ответ:

```json
{
  "session_id": "phone-1",
  "turn_id": "…",
  "text": "Подготовил письмо маме: «…». Подтверждаете отправку?",
  "sources": [{"title":"…","url":"…","snippet":"…"}],
  "draft": {
    "id": "…",
    "channel": "email",
    "recipient": "Мама",
    "text": "…",
    "confirm_token": "…",
    "expires_at": "2026-11-25T12:00:00"
  },
  "action_result": null,
  "provider": "local",
  "usage": {}
}
```

`Idempotency-Key` (опционально) — ключ идемпотентности; повтор с тем же ключом вернёт сохранённый ответ.

## POST /v1/stt

`multipart/form-data`: `file` (любой аудиоформат, поддерживаемый ffmpeg) и опционально `language`.
Ответ: `{"text":"…","language":"ru","duration_ms":2100}`.

## POST /v1/tts

```json
{"text":"Скажите, чем могу помочь?", "format":"mp3", "voice":"ru_RU-irina-medium"}
```

Ответ: бинарное аудио, `Content-Type: audio/mpeg | audio/wav | audio/ogg`.

## POST /v1/voice

`multipart/form-data`: `file`, `session_id`, опционально `locale`.
Ответ: аудио с заголовками `X-Transcript`, `X-Assistant-Text`, `X-Turn-Id`, `X-Draft-Id` и `X-Confirm-Token` (если создан черновик). Текстовые заголовки закодированы в URL-encoded UTF-8. Клиент может взять текст из заголовка или повторить `/v1/chat`.

## WebSocket /v1/ws/voice?session_id=…&token=…

Клиент → сервер:
- binary frame: чанк аудио PCM16 16 kHz mono (рекомендуется 20–100 мс);
- `{"type":"end"}` — конец речи, запуск обработки;
- `{"type":"cancel"}` — отмена текущего turn.

Сервер → клиент:
- `{"type":"status","stage":"listening|transcribing|thinking|speaking"}`;
- `{"type":"transcript","text":"…","final":true}`;
- `{"type":"delta","text":"…"}` — потоковые токены ответа;
- `{"type":"answer","text":"…","sources":[…]}`;
- `{"type":"action_draft","draft":{…}}`;
- `{"type":"audio","data":"<base64 mp3>","index":0}` — потоковые чанки озвучки;
- `{"type":"done","turn_id":"…"}`;
- `{"type":"error","message":"…"}`.

Подтверждение голосом: следующий turn с текстом «да, отправь» (или `confirm_token` в сообщении `{"type":"end","confirm_token":"…"}`) вызывает отправку черновика.

## Черновики

- `GET /v1/actions/drafts?session_id=…` — список черновиков сессии (без токенов).
- `POST /v1/actions/{id}/confirm` — `{"confirm_token":"…"}`. Повторный вызов после отправки вернёт `409`.
- `POST /v1/actions/{id}/cancel` — отмена.

Токен подтверждения одноразовый и живёт `CONFIRMATION_TTL_SECONDS` секунд. Отправить можно только черновик в статусе `pending` (или `failed` с числом попыток < 3).

## POST /v1/integrations/max/webhook

Проверка: `X-Max-Secret: <MAX_WEBHOOK_SECRET>`. Тело — событие MAX (`message_created`, `message_callback` и др.). Для `message_created` бекенд асинхронно генерирует ответ и отправляет его тем же транспортом, которым пришло сообщение.

## Коды ошибок

- `401` — нет/неверный API-ключ;
- `404` — черновик не найден;
- `409` — черновик уже отправлен/отменён/истёк или неверный токен;
- `422` — ошибка валидации;
- `502` — все провайдеры LLM/STT/TTS недоступны;
- `503` — сервис отправки (SMTP/MAX) недоступен.
