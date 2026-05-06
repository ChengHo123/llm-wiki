"""add display_name to line_user_bindings

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-05

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "line_user_bindings",
        sa.Column("display_name", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("line_user_bindings", "display_name")
