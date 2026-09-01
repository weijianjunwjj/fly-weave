"""add ticket intake fields

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tickets", sa.Column("issue_type", sa.String(length=32), nullable=True))
    op.add_column(
        "tickets",
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.alter_column("tickets", "updated_at", server_default=None)


def downgrade() -> None:
    op.drop_column("tickets", "updated_at")
    op.drop_column("tickets", "issue_type")
