from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_services
from app.db import get_session
from app.llm import LLMError
from app.schemas import ChatRequest, ChatResponse
from app.security import get_rate_limiter, idempotent_response, require_api_key, store_idempotent

router = APIRouter(prefix="/v1", tags=["chat"], dependencies=[Depends(require_api_key)])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    db: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ChatResponse:
    services = get_services()
    await get_rate_limiter().check(request.session_id)

    cached = await idempotent_response(db, idempotency_key, "chat")
    if cached:
        return ChatResponse(**cached)

    try:
        response = await services.orchestrator.handle(
            db=db,
            session_id=request.session_id,
            text=request.text,
            locale=request.locale,
            confirm_token=request.confirm_token,
        )
    except LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    await store_idempotent(
        db, idempotency_key, "chat", response.model_dump(mode="json")
    )
    return response
