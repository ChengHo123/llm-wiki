from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://wiki:wiki@localhost:5432/wiki"
    LLM_API_KEY: str
    LLM_BASE_URL: str  # LiteLLM endpoint，例如 http://your-litellm-host/v1
    LLM_MODEL: str = "gpt-4o"
    UPLOAD_DIR: str = "/app/uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    MAX_WIKI_PAGES: int = 100  # 檢索容量上限；達到即拒絕上傳，避免 query 漏頁
    EST_PAGES_PER_DOC: int = 12  # 預估單份文件 ingest 產出頁數；用於排隊中文件的預留估算
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # Vision model（圖片 ingest 用，走 LiteLLM "vision" alias）
    VISION_MODEL: str = "vision"

    # LINE Bot
    LINE_CHANNEL_SECRET: str = ""
    LINE_CHANNEL_ACCESS_TOKEN: str = ""

    # Frontend URL（用於 LINE bot 推送登入連結）
    FRONTEND_URL: str = "http://localhost:3000"

    # Admin 後台帳密（單一管理員，所有 admin endpoint 共用）
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
