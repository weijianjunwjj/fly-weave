"""add agent intent persistence model

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # agent_intents 以类型化字段（而非自由 JSON）持久化已验证的 intent，
    # 每个 AgentRun 至多一条，通过 agent_run_id 唯一约束 + CASCADE 保证
    # 随 Run 删除而清理，与既有 agent_runs / agent_steps 行为一致。
    op.create_table(
        "agent_intents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("intent_type", sa.String(length=64), nullable=False),
        sa.Column("issue_summary", sa.Text(), nullable=False),
        sa.Column("requested_action", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id"),
    )
    op.create_index(
        op.f("ix_agent_intents_agent_run_id"),
        "agent_intents",
        ["agent_run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_intents_agent_run_id"), table_name="agent_intents")
    op.drop_table("agent_intents")
