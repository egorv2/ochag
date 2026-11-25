from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db import AuditRow, DraftRow, utcnow
from app.integrations.dispatcher import DeliveryError, MessageDispatcher
from app.schemas import ActionResult, DraftItem


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def to_draft_item(row: DraftRow, token: str | None = None) -> DraftItem:
    return DraftItem(
        id=row.id,
        channel=row.channel,
        recipient=row.recipient_display or row.recipient,
        recipient_display=row.recipient_display,
        subject=row.subject,
        text=row.text,
        status=row.status,
        confirm_token=token,
        expires_at=row.expires_at,
    )


class ConfirmationService:
    def __init__(self, settings: Settings, dispatcher: MessageDispatcher) -> None:
        self.settings = settings
        self.dispatcher = dispatcher

    async def create_draft(
        self,
        db: AsyncSession,
        session_id: str,
        channel: str,
        recipient: str,
        recipient_display: str,
        text: str,
        subject: str = "",
    ) -> DraftItem:
        token = secrets.token_urlsafe(24)
        row = DraftRow(
            id=f"draft_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            channel=channel,
            recipient=recipient,
            recipient_display=recipient_display,
            subject=subject,
            text=text,
            status="pending",
            token_hash=_hash(token),
            expires_at=utcnow() + timedelta(seconds=self.settings.confirmation_ttl_seconds),
            idempotency_key=f"draft_{uuid.uuid4().hex[:24]}",
        )
        db.add(row)
        db.add(
            AuditRow(
                id=f"audit_{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                action="draft_created",
                status="ok",
                detail={"draft_id": row.id, "channel": channel},
                created_at=utcnow(),
            )
        )
        await db.commit()
        await db.refresh(row)
        return to_draft_item(row, token)

    async def latest_pending(self, db: AsyncSession, session_id: str) -> DraftRow | None:
        await db.execute(
            update(DraftRow)
            .where(DraftRow.status == "pending", DraftRow.expires_at <= utcnow())
            .values(status="expired")
        )
        await db.commit()
        return await db.scalar(
            select(DraftRow)
            .where(DraftRow.session_id == session_id, DraftRow.status == "pending")
            .order_by(DraftRow.created_at.desc())
            .limit(1)
        )

    async def list_drafts(self, db: AsyncSession, session_id: str) -> list[DraftItem]:
        rows = (
            await db.scalars(
                select(DraftRow)
                .where(DraftRow.session_id == session_id)
                .order_by(DraftRow.created_at.desc())
                .limit(50)
            )
        ).all()
        return [to_draft_item(row) for row in rows]

    async def confirm(
        self,
        db: AsyncSession,
        draft_id: str,
        confirm_token: str,
        session_id: str,
        skip_token_check: bool = False,
    ) -> ActionResult:
        row = await db.get(DraftRow, draft_id)
        if row is None or row.session_id != session_id:
            raise HTTPException(status_code=404, detail="Draft not found")

        if row.status == "sent":
            raise HTTPException(status_code=409, detail="Draft already sent")
        if row.status in {"cancelled", "expired"}:
            raise HTTPException(status_code=409, detail=f"Draft is {row.status}")
        if row.expires_at <= utcnow():
            row.status = "expired"
            await db.commit()
            raise HTTPException(status_code=409, detail="Confirmation token expired")
        if not skip_token_check and row.token_hash != _hash(confirm_token):
            raise HTTPException(status_code=409, detail="Invalid confirmation token")
        if row.attempts >= 3:
            raise HTTPException(status_code=409, detail="Too many delivery attempts")

        # Атомарный переход в sending: второй подтверждающий запрос не пройдёт.
        conditions = [
            DraftRow.id == draft_id,
            DraftRow.status.in_(["pending", "failed"]),
            DraftRow.expires_at > utcnow(),
        ]
        if not skip_token_check:
            conditions.append(DraftRow.token_hash == _hash(confirm_token))
        result = await db.execute(
            update(DraftRow)
            .where(*conditions)
            .values(status="sending", attempts=DraftRow.attempts + 1)
        )
        if result.rowcount != 1:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Draft is no longer confirmable")
        await db.commit()

        try:
            sent = await self.dispatcher.send(
                channel=row.channel,
                recipient=row.recipient,
                text=row.text,
                subject=row.subject,
                idempotency_key=row.idempotency_key,
            )
        except DeliveryError as exc:
            row.status = "failed"
            row.external_id = ""
            db.add(
                AuditRow(
                    id=f"audit_{uuid.uuid4().hex[:16]}",
                    session_id=session_id,
                    action="send_failed",
                    status="error",
                    detail={"draft_id": draft_id, "error": str(exc)[:300]},
                )
            )
            await db.commit()
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        row.status = "sent"
        row.external_id = sent.external_id
        row.sent_at = utcnow()
        db.add(
            AuditRow(
                id=f"audit_{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                action="send_succeeded",
                status="ok",
                detail={
                    "draft_id": draft_id,
                    "channel": row.channel,
                    "recipient": row.recipient_display or row.recipient,
                    "external_id": sent.external_id,
                    "transport": sent.transport,
                },
            )
        )
        await db.commit()
        return ActionResult(
            draft_id=draft_id,
            status="sent",
            channel=row.channel,
            recipient=row.recipient_display or row.recipient,
            external_id=sent.external_id,
            message=f"Отправлено: {row.channel} → {row.recipient_display or row.recipient}",
        )

    async def cancel(self, db: AsyncSession, draft_id: str, session_id: str) -> ActionResult:
        row = await db.get(DraftRow, draft_id)
        if row is None or row.session_id != session_id:
            raise HTTPException(status_code=404, detail="Draft not found")
        if row.status != "pending":
            raise HTTPException(status_code=409, detail=f"Draft is {row.status}, cannot cancel")
        row.status = "cancelled"
        db.add(
            AuditRow(
                id=f"audit_{uuid.uuid4().hex[:16]}",
                session_id=session_id,
                action="draft_cancelled",
                status="ok",
                detail={"draft_id": draft_id},
            )
        )
        await db.commit()
        return ActionResult(draft_id=draft_id, status="cancelled", message="Черновик отменён")
