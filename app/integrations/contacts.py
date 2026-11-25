from __future__ import annotations

import json
from dataclasses import dataclass

from app.config import Settings


@dataclass(frozen=True)
class Contact:
    alias: str
    name: str
    channel: str  # email | max
    recipient: str


class ContactBook:
    """Allowlist: LLM may only address known contacts."""

    def __init__(self, settings: Settings) -> None:
        raw = json.loads(settings.contacts_json or "{}")
        self.require_allowlist = settings.require_contact_allowlist
        self.contacts: list[Contact] = []
        for alias, value in raw.items():
            if not isinstance(value, dict):
                continue
            channel = str(value.get("channel", "")).lower()
            if channel not in {"email", "max"}:
                continue
            self.contacts.append(
                Contact(
                    alias=str(alias),
                    name=str(value.get("name") or alias),
                    channel=channel,
                    recipient=str(value.get("recipient") or ""),
                )
            )

    def resolve(self, hint: str, channel: str | None = None) -> Contact | None:
        normalized = (hint or "").strip().lower()
        candidates = [
            c
            for c in self.contacts
            if not channel or c.channel == channel.lower()
        ]
        for contact in candidates:
            if normalized and (
                normalized == contact.alias.lower()
                or normalized == contact.name.lower()
                or normalized in contact.name.lower()
                or normalized in contact.alias.lower()
            ):
                return contact
        return None

    def find_by_recipient(self, recipient: str, channel: str | None = None) -> Contact | None:
        for contact in self.contacts:
            if contact.recipient == recipient and (not channel or contact.channel == channel):
                return contact
        return None

    def as_tool_context(self) -> list[dict[str, str]]:
        return [
            {
                "alias": c.alias,
                "name": c.name,
                "channel": c.channel,
                "recipient": c.recipient if not self.require_allowlist else c.alias,
            }
            for c in self.contacts
        ]
