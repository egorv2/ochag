from __future__ import annotations

from tests.conftest import HEADERS


def test_answer_uses_search_tool(client):
    response = client.post(
        "/v1/chat",
        headers=HEADERS,
        json={"session_id": "search-1", "text": "Сколько варить макароны?"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sources"], "search tool should provide sources"
    assert "макароны" in body["text"].lower()
    assert body["draft"] is None


def test_draft_requires_confirmation_before_send(client):
    response = client.post(
        "/v1/chat",
        headers=HEADERS,
        json={"session_id": "email-1", "text": "Напиши маме что задерживаюсь"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["draft"] is not None
    assert body["draft"]["channel"] == "email"
    assert body["action_result"] is None
    assert "Подтверждаете" in body["text"]

    drafts = client.get("/v1/actions/drafts", headers=HEADERS, params={"session_id": "email-1"})
    assert drafts.status_code == 200
    assert drafts.json()[0]["status"] == "pending"

    bad = client.post(
        f"/v1/actions/{body['draft']['id']}/confirm",
        headers=HEADERS,
        json={"confirm_token": "wrong"},
    )
    assert bad.status_code == 409

    good = client.post(
        f"/v1/actions/{body['draft']['id']}/confirm",
        headers=HEADERS,
        json={"confirm_token": body["draft"]["confirm_token"]},
    )
    assert good.status_code == 200, good.text
    assert good.json()["status"] == "sent"

    repeat = client.post(
        f"/v1/actions/{body['draft']['id']}/confirm",
        headers=HEADERS,
        json={"confirm_token": body["draft"]["confirm_token"]},
    )
    assert repeat.status_code == 409


def test_voice_confirmation_sends_to_max(client):
    first = client.post(
        "/v1/chat",
        headers=HEADERS,
        json={
            "session_id": "max-voice-1",
            "text": "Отправь ребенку сообщение что я задерживаюсь",
        },
    )
    assert first.status_code == 200, first.text
    draft = first.json()["draft"]
    assert draft is not None
    assert draft["channel"] == "max"

    second = client.post(
        "/v1/chat",
        headers=HEADERS,
        json={
            "session_id": "max-voice-1",
            "text": "Да, отправь",
            "confirm_token": draft["confirm_token"],
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["action_result"] is not None
    assert second.json()["action_result"]["status"] == "sent"


def test_same_turn_cannot_self_confirm(client):
    # Даже если модель вызвала draft и confirm в одном turn, отправки не будет.
    response = client.post(
        "/v1/chat",
        headers=HEADERS,
        json={"session_id": "guard-1", "text": "Напиши маме что задерживаюсь, и отправь это"},
    )
    assert response.status_code == 200
    assert response.json()["action_result"] is None


def test_unknown_contact_is_rejected(client):
    response = client.post(
        "/v1/chat",
        headers=HEADERS,
        json={"session_id": "guard-2", "text": "Напиши соседу Васе сообщение про ключи"},
    )
    assert response.status_code == 200
    assert response.json()["draft"] is None


def test_idempotency(client):
    payload = {"session_id": "idem-1", "text": "Привет!"}
    headers = {**HEADERS, "Idempotency-Key": "fixed-key-1"}
    first = client.post("/v1/chat", headers=headers, json=payload).json()
    second = client.post("/v1/chat", headers=headers, json=payload).json()
    assert first["turn_id"] == second["turn_id"]
