from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_services
from app.db import DraftRow, get_session
from app.schemas import ActionResult, ConfirmRequest, DraftItem
from app.security import require_api_key

router = APIRouter(
    prefix="/v1/actions", tags=["actions"], dependencies=[Depends(require_api_key)]
)


@router.get("/drafts", response_model=list[DraftItem])
async def list_drafts(
    session_id: str, db: AsyncSession = Depends(get_session)
) -> list[DraftItem]:
    services = get_services()
    return await services.confirmations.list_drafts(db, session_id)


@router.post("/{draft_id}/confirm", response_model=ActionResult)
async def confirm_draft(
    draft_id: str,
    request: ConfirmRequest,
    db: AsyncSession = Depends(get_session),
) -> ActionResult:
    services = get_services()
    row = await db.get(DraftRow, draft_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Draft not found")
    return await services.confirmations.confirm(
        db=db,
        draft_id=draft_id,
        confirm_token=request.confirm_token,
        session_id=row.session_id,
    )


@router.post("/{draft_id}/cancel", response_model=ActionResult)
async def cancel_draft(
    draft_id: str,
    session_id: str,
    db: AsyncSession = Depends(get_session),
) -> ActionResult:
    services = get_services()
    return await services.confirmations.cancel(db, draft_id, session_id)
