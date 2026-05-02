import asyncio
import uuid
import logging

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.document import Document

logger = logging.getLogger(__name__)

_queue: asyncio.Queue[uuid.UUID] = asyncio.Queue()
_worker_task: asyncio.Task | None = None


async def enqueue(document_id: uuid.UUID) -> None:
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
        logger.info("Processing document %s (queue remaining: %d)", document_id, _queue.qsize())
        await _set_status(document_id, "processing")
        try:
            await run_ingest(document_id)
            logger.info("Finished document %s", document_id)
        except Exception as e:
            logger.error("Ingest failed for %s: %s", document_id, e)
        finally:
            _queue.task_done()


async def _requeue_pending() -> None:
    """啟動時把 queued/processing 的文件重新入列（crash recovery）。
    run_ingest 會以「成功後刪 stale」方式自動清掉上次跑一半的殘留頁面。
    """
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
