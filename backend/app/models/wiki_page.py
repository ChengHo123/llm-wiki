import uuid
from datetime import datetime
from sqlalchemy import String, DateTime, Text, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, TSVECTOR

from app.db.base import Base


class WikiPage(Base):
    __tablename__ = "wiki_pages"
    __table_args__ = (
        Index("ix_wiki_pages_api_key_slug", "api_key_id", "slug", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_key_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("api_keys.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    slug: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_type: Mapped[str] = mapped_column(String(50), default="concept")  # index | summary | entity | concept
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    api_key: Mapped["ApiKey"] = relationship("ApiKey", back_populates="wiki_pages")
    outgoing_links: Mapped[list["WikiLink"]] = relationship(
        "WikiLink", foreign_keys="WikiLink.source_page_id", back_populates="source_page", cascade="all, delete-orphan"
    )
    incoming_links: Mapped[list["WikiLink"]] = relationship(
        "WikiLink", foreign_keys="WikiLink.target_page_id", back_populates="target_page"
    )
