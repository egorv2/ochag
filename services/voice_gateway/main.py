"""OpenAI-compatible STT/TTS gateway running on the same host as the GPU.

- POST /v1/audio/transcriptions  (multipart: file, model, language)
- POST /v1/audio/speech          (JSON: input, model, voice, response_format)

Backends are selected by env: whisper/piper for local models, dummy for
development without models. Whisper is lazily loaded and unloaded after idle.
"""
from __future__ import annotations

import asyncio
import io
import math
import os
import subprocess
import tempfile
import time
import wave
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

STT_BACKEND = os.getenv("STT_BACKEND", "dummy").lower()
TTS_BACKEND = os.getenv("TTS_BACKEND", "dummy").lower()
STT_MODEL_SIZE = os.getenv("STT_MODEL_SIZE", "small")
STT_DEVICE = os.getenv("STT_DEVICE", "cuda")
STT_COMPUTE_TYPE = os.getenv("STT_COMPUTE_TYPE", "int8_float16")
STT_IDLE_UNLOAD_SECONDS = int(os.getenv("STT_IDLE_UNLOAD_SECONDS", "120"))
STT_MOCK_TEXT = os.getenv("STT_MOCK_TEXT", "Сколько по времени варить макароны?")
PIPER_MODEL_PATH = os.getenv("PIPER_MODEL_PATH", "/models/piper/ru_RU-irina-medium.onnx")
PIPER_CONFIG_PATH = os.getenv("PIPER_CONFIG_PATH", "")

_whisper = None
_piper = None
_last_used = time.monotonic()
_gpu_lock = asyncio.Lock()
_unload_task: asyncio.Task | None = None


def _load_whisper():
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel

        _whisper = WhisperModel(
            STT_MODEL_SIZE, device=STT_DEVICE, compute_type=STT_COMPUTE_TYPE
        )
    return _whisper


def _load_piper():
    global _piper
    if _piper is None:
        from piper import PiperVoice

        if not os.path.isfile(PIPER_MODEL_PATH):
            raise RuntimeError(f"Piper model not found: {PIPER_MODEL_PATH}")
        _piper = PiperVoice.load(
            PIPER_MODEL_PATH,
            config_path=PIPER_CONFIG_PATH or None,
            use_cuda=False,  # Piper остаётся на CPU, чтобы не делить 4 ГБ VRAM
        )
    return _piper


async def _unload_loop() -> None:
    global _whisper
    while True:
        await asyncio.sleep(10)
        if _whisper is not None and time.monotonic() - _last_used > STT_IDLE_UNLOAD_SECONDS:
            async with _gpu_lock:
                _whisper = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _unload_task
    _unload_task = asyncio.create_task(_unload_loop())
    yield
    if _unload_task:
        _unload_task.cancel()


app = FastAPI(title="Voice Gateway", version="1.0.0", lifespan=lifespan)


def _to_16k_wav(data: bytes) -> bytes:
    """ffmpeg converts any mobile codec (opus/m4a/mp3) to PCM16 mono 16 kHz."""
    with tempfile.NamedTemporaryFile(suffix=".input", delete=False) as src:
        src.write(data)
        src_path = src.name
    dst_path = src_path + ".wav"
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error", "-i", src_path,
                "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst_path,
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", "replace")[:300])
        with open(dst_path, "rb") as handle:
            return handle.read()
    finally:
        for path in (src_path, dst_path):
            if os.path.exists(path):
                os.unlink(path)


def _convert_wav(wav_bytes: bytes, fmt: str) -> tuple[bytes, str]:
    if fmt == "wav":
        return wav_bytes, "audio/wav"
    codec = {"mp3": ("libmp3lame", "audio/mpeg"), "opus": ("libopus", "audio/ogg")}.get(fmt)
    if codec is None:
        raise RuntimeError(f"Unsupported response_format: {fmt}")
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as src:
        src.write(wav_bytes)
        src_path = src.name
    dst_path = src_path + "." + fmt
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", src_path, "-c:a", codec[0], dst_path],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode("utf-8", "replace")[:300])
        with open(dst_path, "rb") as handle:
            return handle.read(), codec[1]
    finally:
        for path in (src_path, dst_path):
            if os.path.exists(path):
                os.unlink(path)


def _tone_wav() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22_050)
        frames = []
        for i in range(int(0.3 * 22_050)):
            frames.append(
                int(500 * math.sin(2 * math.pi * 440 * i / 22_050)).to_bytes(2, "little", signed=True)
            )
        wav.writeframes(b"".join(frames))
    return buffer.getvalue()


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "stt_backend": STT_BACKEND,
        "tts_backend": TTS_BACKEND,
    }


@app.post("/v1/audio/transcriptions")
async def transcriptions(
    file: UploadFile = File(...),
    model: str = Form("small"),
    language: str = Form("ru"),
) -> dict:
    global _last_used
    audio = await file.read()
    if STT_BACKEND == "dummy":
        return {"text": STT_MOCK_TEXT, "language": language, "duration_ms": 0}
    if STT_BACKEND != "whisper":
        raise HTTPException(status_code=500, detail=f"Unknown STT backend: {STT_BACKEND}")
    try:
        wav_bytes = await asyncio.to_thread(_to_16k_wav, audio)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid audio: {exc}") from exc
    try:
        async with _gpu_lock:
            voice = await asyncio.to_thread(_load_whisper)
            segments, info = await asyncio.to_thread(
                voice.transcribe, io.BytesIO(wav_bytes), language=language, beam_size=5
            )
            text = "".join(segment.text for segment in segments).strip()
            _last_used = time.monotonic()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"STT error: {exc}") from exc
    return {
        "text": text,
        "language": getattr(info, "language", language),
        "duration_ms": int(getattr(info, "duration", 0.0) * 1000),
    }


class SpeechRequest(BaseModel):
    input: str
    model: str = "ru_RU-irina-medium"
    voice: str = "ru_RU-irina-medium"
    response_format: str = "wav"


@app.post("/v1/audio/speech")
async def speech(request: SpeechRequest) -> Response:
    if TTS_BACKEND == "dummy":
        audio, content_type = _convert_wav(_tone_wav(), request.response_format)
        return Response(content=audio, media_type=content_type)
    if TTS_BACKEND != "piper":
        raise HTTPException(status_code=500, detail=f"Unknown TTS backend: {TTS_BACKEND}")
    try:
        voice = await asyncio.to_thread(_load_piper)
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            voice.synthesize(request.input, wav_file)
        audio, content_type = _convert_wav(wav_buffer.getvalue(), request.response_format)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"TTS error: {exc}") from exc
    return Response(content=audio, media_type=content_type)
