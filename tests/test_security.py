from __future__ import annotations


def test_api_key_required(client):
    response = client.post(
        "/v1/chat", json={"session_id": "s", "text": "привет"}
    )
    assert response.status_code == 401


def test_webhook_secret(client):
    response = client.post(
        "/v1/integrations/max/webhook",
        json={"type": "message_created", "message": {"body": {"text": "привет"}}},
    )
    assert response.status_code == 401

    ok = client.post(
        "/v1/integrations/max/webhook",
        headers={"X-Max-Secret": "change-me"},
        json={"type": "bot_started"},
    )
    assert ok.status_code == 200
