from __future__ import annotations

import base64
import json
import re
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_services
from app.db import get_session, get_session_factory
from app.llm import LLMError
from app.schemas import ChatResponse, TTSRequest
from app.security import check_ws_token, get_rate_limiter, require_api_key
from app.services.audio_client import AudioError

router = APIRouter(prefix="/v1", tags=["voice"])

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _latin1_header(value: str) -> str:
    return urllib.parse.quote(value, safe="")


@router.post("/stt", dependencies=[Depends(require_api_key)])
async def stt(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> dict:
    services = get_services()
    try:
        return await services.audio.transcribe(
            await file.read(), filename=file.filename or "audio.webm", language=language
        )
    except AudioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/tts", dependencies=[Depends(require_api_key)])
async def tts(request: TTSRequest) -> Response:
    services = get_services()
    try:
        audio, content_type = await services.audio.synthesize(
            request.text, request.format, request.voice
        )
    except AudioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(content=audio, media_type=content_type)


@router.post("/voice", dependencies=[Depends(require_api_key)])
async def voice_turn(
    file: UploadFile = File(...),
    session_id: str = Form(...),
    locale: str = Form("ru-RU"),
    db: AsyncSession = Depends(get_session),
) -> Response:
    services = get_services()
    await get_rate_limiter().check(session_id)
    try:
        transcript = await services.audio.transcribe(
            await file.read(), filename=file.filename or "audio.webm", language=locale.split("-")[0]
        )
        answer: ChatResponse = await services.orchestrator.handle(
            db=db, session_id=session_id, text=transcript["text"], locale=locale
        )
        audio, content_type = await services.audio.synthesize(answer.text)
    except AudioError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    headers = {
        "X-Transcript": _latin1_header(transcript["text"]),
        "X-Assistant-Text": _latin1_header(answer.text),
        "X-Turn-Id": answer.turn_id,
    }
    if answer.draft:
        headers["X-Draft-Id"] = answer.draft.id
        headers["X-Confirm-Token"] = answer.draft.confirm_token or ""
    return Response(content=audio, media_type=content_type, headers=headers)


@router.websocket("/ws/voice")
async def ws_voice(websocket: WebSocket, session_id: str, token: str | None = None) -> None:
    try:
        check_ws_token(token)
    except HTTPException:
        await websocket.close(code=1008, reason="Unauthorized")
        return
    await websocket.accept()
    services = get_services()
    buffer = bytearray()
    confirm_token: str | None = None

    async def send(event: dict) -> None:
        await websocket.send_text(json.dumps(event, ensure_ascii=False))

    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break

            if message.get("bytes"):
                if len(buffer) + len(message["bytes"]) > services.settings.max_audio_bytes:
                    await send({"type": "error", "message": "Audio buffer too large"})
                    buffer.clear()
                    continue
                buffer.extend(message["bytes"])
                if len(buffer) % 32_000 < 2000:  # примерный индикатор активности
                    await send({"type": "status", "stage": "listening"})
                continue

            raw_text = message.get("text")
            if not raw_text:
                continue
            try:
                command = json.loads(raw_text)
            except json.JSONDecodeError:
                await send({"type": "error", "message": "Invalid JSON"})
                continue

            kind = command.get("type")
            if kind == "cancel":
                buffer.clear()
                await send({"type": "status", "stage": "cancelled"})
                continue
            if kind != "end":
                continue

            await get_rate_limiter().check(session_id)
            if not buffer:
                await send({"type": "error", "message": "No audio received"})
                continue
            await send({"type": "status", "stage": "transcribing"})
            audio_bytes = bytes(buffer)
            buffer.clear()
            try:
                transcript = await services.audio.transcribe(
                    audio_bytes, filename="chunk.wav", language="ru"
                )
            except AudioError as exc:
                await send({"type": "error", "message": str(exc)})
                continue
            await send(
                {"type": "transcript", "text": transcript["text"], "final": True}
            )

            token_for_turn = command.get("confirm_token") or confirm_token
            sentence = ""

            async def on_event(event: dict) -> None:
                nonlocal sentence, confirm_token
                if event["type"] == "delta":
                    await send(event)
                    sentence += event["text"]
                    if _SENTENCE_END.search(sentence):
                        ready, sentence = _SENTENCE_END.split(sentence, maxsplit=1)
                        await _stream_audio(ready)
                elif event["type"] == "action_draft":
                    confirm_token = event["draft"].get("confirm_token")
                    await send(event)
                elif event["type"] != "delta":
                    await send(event)

            async def _stream_audio(text: str) -> None:
                if not text.strip():
                    return
                try:
                    audio, _content_type = await services.audio.synthesize(text)
                except AudioError:
                    return
                index = int(command.get("audio_index", 0))
                await send(
                    {
                        "type": "audio",
                        "data": base64.b64encode(audio).decode("ascii"),
                        "index": index,
                    }
                )
                command["audio_index"] = index + 1

            try:
                factory = get_session_factory()
                async with factory() as db:
                    await services.orchestrator.handle(
                        db=db,
                        session_id=session_id,
                        text=transcript["text"],
                        on_event=on_event,
                        confirm_token=token_for_turn,
                    )
                await _stream_audio(sentence)
            except Exception as exc:
                await send({"type": "error", "message": str(exc)[:300]})
    except WebSocketDisconnect:
        return
