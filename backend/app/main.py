from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.api.v1 import auth, documents, wiki, query

settings = get_settings()

app = FastAPI(
    title="LLM Wiki API",
    description="個人 wiki 知識庫平台 — 基於 Karpathy LLM Wiki 模式",
    version="1.0.0",
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


@app.get("/health")
async def health():
    return {"status": "ok"}
