from __future__ import annotations

from tests.conftest import HEADERS


def test_stt_and_tts(client):
    stt = client.post(
        "/v1/stt",
        headers=HEADERS,
        files={"file": ("audio.wav", b"fake", "audio/wav")},
    )
    assert stt.status_code == 200, stt.text
    assert stt.json()["text"]

    tts = client.post(
        "/v1/tts",
        headers=HEADERS,
        json={"text": "Привет", "format": "wav"},
    )
    assert tts.status_code == 200, tts.text
    assert tts.headers["content-type"].startswith("audio/")
    assert tts.content[:4] == b"RIFF"


def test_voice_turn_returns_audio_and_headers(client):
    response = client.post(
        "/v1/voice",
        headers=HEADERS,
        files={"file": ("audio.wav", b"fake", "audio/wav")},
        data={"session_id": "voice-1"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["x-turn-id"].startswith("turn_")
    assert response.content[:4] == b"RIFF"


def test_websocket_stream(client):
    with client.websocket_connect("/v1/ws/voice?session_id=ws-1&token=test-key") as ws:
        ws.send_bytes(b"fake-audio-chunk")
        ws.send_text('{"type":"end"}')
        events = []
        for _ in range(20):
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "done":
                break
    types = [event["type"] for event in events]
    assert "transcript" in types
    assert "answer" in types
    assert "done" in types
