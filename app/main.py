from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import actions, chat, integrations, voice
from app.config import get_settings
from app.db import init_db
from app.openapi import install_openapi

logging.basicConfig(level=get_settings().log_level)
logger = logging.getLogger("assistant")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    logger.info("Database initialized")
    yield


app = FastAPI(title="Voice Assistant Backend", version="1.0.0", lifespan=lifespan)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(voice.router)
app.include_router(actions.router)
app.include_router(integrations.router)

install_openapi(app)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}


@app.get("/ready")
async def ready() -> dict[str, str]:
    from sqlalchemy import text

    from app.db import get_engine

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc
    return {"status": "ready"}
