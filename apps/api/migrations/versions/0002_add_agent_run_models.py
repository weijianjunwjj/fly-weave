"""add agent run and agent step persistence models

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 与 0001 保持一致：create_type=False 阻止这些枚举在被用作列类型时
# （op.create_table 内部）触发隐式的第二次 CREATE TYPE。枚举类型的创建
# 统一由 upgrade() 中显式的 .create(checkfirst=True) 调用负责。
agent_run_status_enum = PG_ENUM(
    "queued", "running", "waiting_for_approval", "completed", "failed", "cancelled",
    name="agentrunstatus",
    create_type=False,
)
agent_step_status_enum = PG_ENUM(
    "pending", "running", "completed", "failed", "skipped",
    name="agentstepstatus",
    create_type=False,
)


def upgrade() -> None:
    agent_run_status_enum.create(op.get_bind(), checkfirst=True)
    agent_step_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=64), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("status", agent_run_status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(op.f("ix_agent_runs_business_key"), "agent_runs", ["business_key"], unique=True)
    op.create_index(op.f("ix_agent_runs_ticket_id"), "agent_runs", ["ticket_id"], unique=False)

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("step_order", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("status", agent_step_status_enum, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id", "step_order", name="uq_agent_steps_run_step_order"),
    )
    op.create_index(op.f("ix_agent_steps_agent_run_id"), "agent_steps", ["agent_run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_agent_steps_agent_run_id"), table_name="agent_steps")
    op.drop_table("agent_steps")
    op.drop_index(op.f("ix_agent_runs_ticket_id"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_business_key"), table_name="agent_runs")
    op.drop_table("agent_runs")
    agent_step_status_enum.drop(op.get_bind(), checkfirst=True)
    agent_run_status_enum.drop(op.get_bind(), checkfirst=True)
