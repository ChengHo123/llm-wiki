"""add wiki_page_sources for many-to-many tracking

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-07

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wiki_page_sources",
        sa.Column(
            "wiki_page_id",
            UUID(as_uuid=True),
            sa.ForeignKey("wiki_pages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_wiki_page_sources_document_id",
        "wiki_page_sources",
        ["document_id"],
    )

    # Backfill：把現有 wiki_pages.source_document_id 寫進關聯表，
    # 避免升級後既有頁面失去 source 連結（會被視為孤兒）。
    op.execute("""
        INSERT INTO wiki_page_sources (wiki_page_id, document_id, created_at)
        SELECT id, source_document_id, COALESCE(created_at, NOW())
        FROM wiki_pages
        WHERE source_document_id IS NOT NULL
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index("ix_wiki_page_sources_document_id", table_name="wiki_page_sources")
    op.drop_table("wiki_page_sources")
