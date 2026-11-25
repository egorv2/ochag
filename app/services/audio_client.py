from __future__ import annotations

import io
import math
import wave

import httpx

from app.config import Settings


class AudioError(RuntimeError):
    pass


class AudioClient:
    """OpenAI-compatible STT/TTS client plus dependency-free mock backends."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def transcribe(
        self, audio: bytes, filename: str = "audio.webm", language: str | None = None
    ) -> dict:
        if len(audio) > self.settings.max_audio_bytes:
            raise AudioError("Audio file is too large")
        if self.settings.stt_type == "mock":
            return {
                "text": self.settings.stt_mock_text,
                "language": language or self.settings.stt_language,
                "duration_ms": 0,
            }
        url = f"{self.settings.stt_base_url.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.settings.stt_api_key}"}
        data = {"model": self.settings.stt_model}
        if language:
            data["language"] = language
        try:
            async with httpx.AsyncClient(timeout=self.settings.stt_timeout) as client:
                response = await client.post(
                    url,
                    files={"file": (filename, audio, "application/octet-stream")},
                    data=data,
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            raise AudioError(f"STT failed: {type(exc).__name__}: {exc}") from exc
        return {
            "text": str(payload.get("text") or ""),
            "language": str(payload.get("language") or language or ""),
            "duration_ms": int(payload.get("duration_ms") or 0),
        }

    async def synthesize(
        self, text: str, response_format: str | None = None, voice: str | None = None
    ) -> tuple[bytes, str]:
        fmt = response_format or self.settings.tts_format
        if self.settings.tts_type == "mock":
            return _tone_wav(), "audio/wav"
        url = f"{self.settings.tts_base_url.rstrip('/')}/audio/speech"
        headers = {"Authorization": f"Bearer {self.settings.tts_api_key}"}
        payload = {
            "model": self.settings.tts_model,
            "input": text,
            "voice": voice or self.settings.tts_voice,
            "response_format": fmt,
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.tts_timeout) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except Exception as exc:
            raise AudioError(f"TTS failed: {type(exc).__name__}: {exc}") from exc
        content_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, content_type.split(";")[0].strip()


def _tone_wav(seconds: float = 0.3, sample_rate: int = 22_050) -> bytes:
    buffer = io.BytesIO()
    frames = int(seconds * sample_rate)
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        samples = bytearray()
        for i in range(frames):
            envelope = min(1.0, i / (sample_rate * 0.05), (frames - i) / (sample_rate * 0.05))
            value = int(900 * envelope * math.sin(2 * math.pi * 440 * i / sample_rate))
            samples.extend(value.to_bytes(2, "little", signed=True))
        wav.writeframes(samples)
    return buffer.getvalue()
