from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.v1 import admin, auth, documents, wiki, query, linebot
from app.api.v1.linebot import warmup_line_client, close_line_client
from app.services.ingest_queue import start_worker, stop_worker

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_worker()
    await warmup_line_client()
    yield
    await stop_worker()
    await close_line_client()


app = FastAPI(
    title="LLM Wiki API",
    description="個人 wiki 知識庫平台 — 基於 Karpathy LLM Wiki 模式",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1", tags=["Auth"])
app.include_router(documents.router, prefix="/api/v1", tags=["Documents"])
app.include_router(wiki.router, prefix="/api/v1", tags=["Wiki"])
app.include_router(query.router, prefix="/api/v1", tags=["Query"])
app.include_router(linebot.router, prefix="/api/v1", tags=["LINE Bot"])
app.include_router(admin.router, prefix="/api/v1", tags=["Admin"])


@app.get("/health")
async def health():
    return {"status": "ok"}
