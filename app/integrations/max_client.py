from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.config import Settings


class DeliveryError(RuntimeError):
    pass


@dataclass
class SendResult:
    external_id: str
    transport: str


class _RateLimiter:
    """Token bucket for MAX outgoing messages (platform limit ≈2 msg/sec/chat)."""

    def __init__(self, rate: float) -> None:
        self.interval = 1.0 / rate if rate > 0 else 0.0
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        if not self.interval:
            return
        async with self._lock:
            now = time.monotonic()
            wait = self._last + self.interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class MaxTransportBase:
    name = "base"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.limiter = _RateLimiter(settings.max_bot_rate_limit_per_sec)

    async def send(
        self, recipient: str, text: str, idempotency_key: str = ""
    ) -> SendResult:
        raise NotImplementedError


class MockMaxTransport(MaxTransportBase):
    name = "mock"

    async def send(
        self, recipient: str, text: str, idempotency_key: str = ""
    ) -> SendResult:
        await self.limiter.acquire()
        return SendResult(external_id=f"mock-{idempotency_key or recipient}", transport=self.name)


class MaxBotTransport(MaxTransportBase):
    """Official MAX Bot API: https://dev.max.ru/docs-api"""

    name = "max_bot"

    async def send(
        self, recipient: str, text: str, idempotency_key: str = ""
    ) -> SendResult:
        if not self.settings.max_bot_token:
            raise DeliveryError("MAX_BOT_TOKEN is not configured")
        await self.limiter.acquire()
        url = f"{self.settings.max_api_base.rstrip('/')}/messages"
        headers = {"Authorization": self.settings.max_bot_token}
        async with httpx.AsyncClient(timeout=20.0) as client:
            for attempt in range(2):
                response = await client.post(
                    url,
                    params={"user_id": recipient},
                    json={"text": text, "format": "markdown"},
                    headers=headers,
                )
                if response.status_code == 429 and attempt == 0:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    await asyncio.sleep(min(retry_after, 5))
                    continue
                if response.status_code >= 400:
                    raise DeliveryError(f"MAX Bot API error {response.status_code}: {response.text[:300]}")
                payload = response.json()
                if payload.get("success") is False:
                    raise DeliveryError(f"MAX Bot API error: {payload}")
                return SendResult(
                    external_id=str((payload.get("message") or {}).get("mid") or payload.get("mid") or ""),
                    transport=self.name,
                )
        raise DeliveryError("MAX Bot API rate limit exceeded")


class MaxUserBridgeTransport(MaxTransportBase):
    """HTTP contract for a companion app that owns the user's MAX session.

    No unofficial protocol is implemented here: the bridge is an independent
    service deployed by the operator. See docs/DESIGN.md for the legal risk.
    """

    name = "max_user_bridge"

    async def send(
        self, recipient: str, text: str, idempotency_key: str = ""
    ) -> SendResult:
        if not self.settings.max_user_bridge_url:
            raise DeliveryError("MAX_USER_BRIDGE_URL is not configured")
        await self.limiter.acquire()
        headers = {"Content-Type": "application/json"}
        if self.settings.max_user_bridge_token:
            headers["Authorization"] = f"Bearer {self.settings.max_user_bridge_token}"
        payload = {
            "recipient": recipient,
            "text": text,
            "idempotency_key": idempotency_key,
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{self.settings.max_user_bridge_url.rstrip('/')}/v1/messages/send",
                json=payload,
                headers=headers,
            )
        if response.status_code >= 400:
            raise DeliveryError(f"MAX bridge error {response.status_code}: {response.text[:300]}")
        data = response.json() if response.content else {}
        return SendResult(
            external_id=str(data.get("external_id") or data.get("mid") or ""),
            transport=self.name,
        )


class DisabledMaxTransport(MaxTransportBase):
    name = "disabled"

    async def send(
        self, recipient: str, text: str, idempotency_key: str = ""
    ) -> SendResult:
        raise DeliveryError("MAX transport is disabled")


def build_max_transport(settings: Settings) -> MaxTransportBase:
    kind = settings.max_transport.lower()
    mapping: dict[str, type[MaxTransportBase]] = {
        "mock": MockMaxTransport,
        "bot": MaxBotTransport,
        "user_bridge": MaxUserBridgeTransport,
        "disabled": DisabledMaxTransport,
    }
    if kind not in mapping:
        raise ValueError(f"Unknown MAX transport: {kind}")
    return mapping[kind](settings)
