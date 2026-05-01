import asyncio
import uuid
import logging

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.document import Document

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[uuid.UUID] = asyncio.Queue()
_worker_task: asyncio.Task | None = None
_cancelled: set[uuid.UUID] = set()


def request_cancel(document_id: uuid.UUID) -> None:
    """要求中止某份文件的 ingest。worker 會在下個 chunk 邊界檢查。"""
    _cancelled.add(document_id)


def is_cancelled(document_id: uuid.UUID) -> bool:
    return document_id in _cancelled


def clear_cancel(document_id: uuid.UUID) -> None:
    _cancelled.discard(document_id)


async def enqueue(document_id: uuid.UUID) -> None:
    clear_cancel(document_id)
    await _queue.put(document_id)
    logger.info("Enqueued document %s (queue size: %d)", document_id, _queue.qsize())


async def _set_status(document_id: uuid.UUID, status: str) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if doc:
            doc.status = status
            await db.commit()


async def _worker() -> None:
    from app.services.ingest import run_ingest
    logger.info("Ingest worker started")
    while True:
        document_id = await _queue.get()
        # 從 queue 拿出後但開工前再檢查一次：可能已被 admin 標 cancelled
        if is_cancelled(document_id):
            logger.info("Document %s cancelled before start, skipping", document_id)
            clear_cancel(document_id)
            _queue.task_done()
            continue
        logger.info("Processing document %s (queue remaining: %d)", document_id, _queue.qsize())
        await _set_status(document_id, "processing")
        try:
            await run_ingest(document_id)
            logger.info("Finished document %s", document_id)
        except Exception as e:
            logger.error("Ingest failed for %s: %s", document_id, e)
        finally:
            clear_cancel(document_id)
            _queue.task_done()


async def _requeue_pending() -> None:
    """啟動時把 queued/processing 的文件重新入列（crash recovery）"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Document).where(Document.status.in_(["queued", "processing"]))
        )
        docs = result.scalars().all()
        for doc in docs:
            doc.status = "queued"
            await _queue.put(doc.id)
        if docs:
            await db.commit()
            logger.info("Re-queued %d documents on startup", len(docs))


async def start_worker() -> None:
    global _worker_task
    await _requeue_pending()
    _worker_task = asyncio.create_task(_worker())


async def stop_worker() -> None:
    if _worker_task:
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
