"""add approval request decision_reason column

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # T021：人工审批决策可选记录一条理由。nullable 表示"可以只 approve / reject
    # 而不写理由"；决策一旦落库，重试不会刷新它，因此无需默认值或回填。
    op.add_column(
        "approval_requests",
        sa.Column("decision_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("approval_requests", "decision_reason")
