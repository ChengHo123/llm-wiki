import os
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from pydantic import BaseModel

from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.document import Document
from app.models.wiki_page import WikiPage
from app.models.wiki_page_source import WikiPageSource
from app.core.security import get_current_key
from app.core.config import get_settings
from app.services.ingest_queue import enqueue

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
    file: UploadFile = File(...),
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    """上傳文件並非同步觸發 ingest"""
    if file.content_type not in ALLOWED_TYPES:
        raise HTTPException(status_code=415, detail=f"不支援的檔案類型: {file.content_type}")

    count_result = await db.execute(
        select(func.count(WikiPage.id)).where(WikiPage.api_key_id == api_key.id)
    )
    current_pages = count_result.scalar_one()

    pending_result = await db.execute(
        select(func.count(Document.id)).where(
            Document.api_key_id == api_key.id,
            Document.status.in_(["queued", "processing"]),
        )
    )
    pending_docs = pending_result.scalar_one()

    projected = current_pages + pending_docs * settings.EST_PAGES_PER_DOC + settings.EST_PAGES_PER_DOC
    if projected > settings.MAX_WIKI_PAGES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Wiki 預估將達 {projected} 頁，超過 {settings.MAX_WIKI_PAGES} 頁上限"
                f"（目前 {current_pages} 頁，排隊中 {pending_docs} 份 x 預估 {settings.EST_PAGES_PER_DOC} 頁）。"
                "請等排隊完成、刪既有文件，或調高 MAX_WIKI_PAGES。"
            ),
        )

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
        status="queued",
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    await enqueue(doc.id)

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


@router.post("/documents/{document_id}/retry")
async def retry_document(
    document_id: uuid.UUID,
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    """重新觸發失敗文件的 ingest"""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.api_key_id == api_key.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文件不存在")
    if doc.status == "processing":
        raise HTTPException(status_code=409, detail="文件正在處理中")

    doc.status = "queued"
    doc.error_message = None
    await db.commit()

    await enqueue(doc.id)

    return DocumentOut(
        id=str(doc.id),
        filename=doc.filename,
        content_type=doc.content_type,
        status=doc.status,
        error_message=doc.error_message,
        created_at=doc.created_at.isoformat(),
    )


@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: uuid.UUID,
    delete_pages: bool = True,
    api_key: ApiKey = Depends(get_current_key),
    db: AsyncSession = Depends(get_db),
):
    """刪除文件。delete_pages=true（預設）時，僅刪除「此文件為唯一 source」的 wiki 頁面；
    其他文件也貢獻過的頁面會保留下來，僅移除這份文件的 source 連結。"""
    result = await db.execute(
        select(Document).where(
            Document.id == document_id,
            Document.api_key_id == api_key.id,
        )
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="文件不存在")

    candidate_page_ids: list[uuid.UUID] = []
    if delete_pages:
        candidates = await db.execute(
            select(WikiPage.id)
            .join(WikiPageSource, WikiPage.id == WikiPageSource.wiki_page_id)
            .where(
                WikiPageSource.document_id == document_id,
                WikiPage.api_key_id == api_key.id,
            )
        )
        candidate_page_ids = [row[0] for row in candidates.all()]

    # 刪除實體檔案
    try:
        Path(doc.file_path).unlink(missing_ok=True)
    except Exception:
        pass

    # 刪文件本身：DB CASCADE 會自動移除 wiki_page_sources 中的對應 row
    await db.delete(doc)
    await db.flush()

    pages_deleted = 0
    if delete_pages:
        for page_id in candidate_page_ids:
            remaining = await db.scalar(
                select(func.count())
                .select_from(WikiPageSource)
                .where(WikiPageSource.wiki_page_id == page_id)
            )
            if remaining == 0:
                page = await db.get(WikiPage, page_id)
                if page is not None:
                    await db.delete(page)
                    pages_deleted += 1

    await db.commit()

    return {"deleted_document_id": str(document_id), "pages_deleted": pages_deleted}
