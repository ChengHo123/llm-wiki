import uuid
from datetime import datetime
from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    documents: Mapped[list["Document"]] = relationship("Document", back_populates="api_key", cascade="all, delete-orphan")
    wiki_pages: Mapped[list["WikiPage"]] = relationship("WikiPage", back_populates="api_key", cascade="all, delete-orphan")
    activity_logs: Mapped[list["ActivityLog"]] = relationship("ActivityLog", back_populates="api_key", cascade="all, delete-orphan")
