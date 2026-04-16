import uuid
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class WikiLink(Base):
    __tablename__ = "wiki_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False)
    target_page_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("wiki_pages.id", ondelete="CASCADE"), nullable=False)
    link_text: Mapped[str | None] = mapped_column(String(255), nullable=True)

    source_page: Mapped["WikiPage"] = relationship("WikiPage", foreign_keys=[source_page_id], back_populates="outgoing_links")
    target_page: Mapped["WikiPage"] = relationship("WikiPage", foreign_keys=[target_page_id], back_populates="incoming_links")
