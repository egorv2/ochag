from __future__ import annotations

from app.config import Settings
from app.integrations.email_client import EmailClient
from app.integrations.max_client import DeliveryError, MaxTransportBase, SendResult, build_max_transport


class MessageDispatcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.email = EmailClient(settings)
        self.max = build_max_transport(settings)

    async def send(
        self,
        channel: str,
        recipient: str,
        text: str,
        subject: str = "",
        idempotency_key: str = "",
    ) -> SendResult:
        if channel == "email":
            if self.settings.email_transport == "mock":
                return SendResult(
                    external_id=f"mock-email-{idempotency_key}", transport="email_mock"
                )
            return await self.email.send(recipient, text, subject, idempotency_key)
        if channel == "max":
            return await self.max.send(recipient, text, idempotency_key)
        raise DeliveryError(f"Unsupported channel: {channel}")


__all__ = ["MessageDispatcher", "DeliveryError", "SendResult"]
