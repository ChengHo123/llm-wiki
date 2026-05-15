from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.log_buffer import setup_log_buffer

setup_log_buffer()
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

# Rich menu / 介紹圖片靜態目錄。LINE Flex carousel 的 imageUrl 從這裡取。
# 目錄不存在就跳過 mount，避免本機沒掛素材目錄時啟動失敗。
# 路徑刻意放在 /api/ 之下：production 的 frontend nginx 只 proxy /api/* 到 backend，
# 其他路徑會落到 try_files → index.html。放 /api/ 才能讓 LINE 從 https://<DOMAIN>/api/static/menu/...
# 抓到實際圖檔（同一條 caddy → nginx → backend 路徑）。
_menu_dir = Path(settings.RICH_MENU_ASSETS_DIR)
if _menu_dir.is_dir():
    app.mount("/api/static/menu", StaticFiles(directory=str(_menu_dir)), name="menu")

app.include_router(auth.router, prefix="/api/v1", tags=["Auth"])
app.include_router(documents.router, prefix="/api/v1", tags=["Documents"])
app.include_router(wiki.router, prefix="/api/v1", tags=["Wiki"])
app.include_router(query.router, prefix="/api/v1", tags=["Query"])
app.include_router(linebot.router, prefix="/api/v1", tags=["LINE Bot"])
app.include_router(admin.router, prefix="/api/v1", tags=["Admin"])


@app.get("/health")
async def health():
    return {"status": "ok"}
