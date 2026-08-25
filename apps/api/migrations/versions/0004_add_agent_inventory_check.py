"""add agent inventory check persistence model

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agent_inventory_checks 以类型化字段（而非自由 JSON）持久化 check_inventory
    # 的真实调用结果，每个 AgentRun 至多一条，通过 agent_run_id 唯一约束 +
    # CASCADE 保证随 Run 删除而清理，与既有 agent_intents 行为一致。
    op.create_table(
        "agent_inventory_checks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("requested_sku", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=True),
        sa.Column("warehouse", sa.String(length=64), nullable=True),
        sa.Column("is_demo_data", sa.Boolean(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id"),
    )
    op.create_index(
        op.f("ix_agent_inventory_checks_agent_run_id"),
        "agent_inventory_checks",
        ["agent_run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_agent_inventory_checks_agent_run_id"),
        table_name="agent_inventory_checks",
    )
    op.drop_table("agent_inventory_checks")
