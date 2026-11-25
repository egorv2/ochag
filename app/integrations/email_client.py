from __future__ import annotations

from email.message import EmailMessage

import aiosmtplib

from app.config import Settings
from app.integrations.max_client import DeliveryError, SendResult


class EmailClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(
        self, recipient: str, text: str, subject: str = "", idempotency_key: str = ""
    ) -> SendResult:
        message = EmailMessage()
        message["From"] = self.settings.smtp_from
        message["To"] = recipient
        message["Subject"] = subject or "Сообщение от голосового помощника"
        message.set_content(text)
        try:
            await aiosmtplib.send(
                message,
                hostname=self.settings.smtp_host,
                port=self.settings.smtp_port,
                username=self.settings.smtp_user or None,
                password=self.settings.smtp_password or None,
                start_tls=self.settings.smtp_use_tls,
                timeout=self.settings.smtp_timeout,
            )
        except Exception as exc:
            raise DeliveryError(f"SMTP error: {type(exc).__name__}: {exc}") from exc
        return SendResult(external_id=f"smtp-{idempotency_key}", transport="smtp")
