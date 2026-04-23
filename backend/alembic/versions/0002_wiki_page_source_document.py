"""add source_document_id to wiki_pages

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-16

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "wiki_pages",
        sa.Column(
            "source_document_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_wiki_pages_source_document_id", "wiki_pages", ["source_document_id"])


def downgrade() -> None:
    op.drop_index("ix_wiki_pages_source_document_id", "wiki_pages")
    op.drop_column("wiki_pages", "source_document_id")
