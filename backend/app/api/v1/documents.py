import os
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.document import Document
from app.core.security import get_current_key
from app.core.config import get_settings
from app.services.ingest import run_ingest

router = APIRouter()
settings = get_settings()

ALLOWED_TYPES = {
    "application/pdf",
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "text/plain", "text/markdown", "text/csv",
    "application/json",
}


class DocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    status: str
    error_message: str | None
    created_at: str

    class Config:
        from_attributes = True


@router.post("/documents", response_model=DocumentOut)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    """上傳文件並非同步觸發 ingest"""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail=f"不支援的檔案類型: {file.content_type}")

    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"檔案超過 {settings.MAX_UPLOAD_SIZE_MB}MB 限制")

    upload_dir = Path(settings.UPLOAD_DIR) / str(api_key.id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4()
    safe_name = f"{file_id}_{file.filename}"
    file_path = upload_dir / safe_name

    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    doc = Document(
        api_key_id=api_key.id,
        filename=file.filename,
        content_type=file.content_type,
        file_path=str(file_path),
        status="pending",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    background_tasks.add_task(run_ingest, doc.id, db)

    return DocumentOut(
        id=str(doc.id),
        filename=doc.filename,
        content_type=doc.content_type,
        status=doc.status,
        error_message=doc.error_message,
        created_at=doc.created_at.isoformat(),
    )


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document)
        .where(Document.api_key_id == api_key.id)
        .order_by(Document.created_at.desc())
    )
    docs = result.scalars().all()
    return [
        DocumentOut(
            id=str(d.id),
            filename=d.filename,
            content_type=d.content_type,
            status=d.status,
            error_message=d.error_message,
            created_at=d.created_at.isoformat(),
        )
        for d in docs
    ]
