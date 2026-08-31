"""add agent policy retrieval persistence

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0009 已把 auditeventtype 建成 PostgreSQL 枚举；T025 需要新增一个
    # policy_retrieved 事件类型。枚举取值来自 models.py 的 AuditEventType，迁移
    # 只做增量 ADD VALUE，不重建枚举、不发明取值。IF NOT EXISTS 保证重复升级幂等。
    #
    # 注意：本迁移只新增取值，不在同一事务里向该枚举列写入任何行（新表
    # agent_policy_retrievals.status 是 String 而非该枚举），因此不会触发
    # PostgreSQL "ADD VALUE 后同事务使用新取值" 的限制。
    op.execute("ALTER TYPE auditeventtype ADD VALUE IF NOT EXISTS 'policy_retrieved'")

    # agent_policy_retrievals 是 T025 "policy retrieval 作为可检查的真实 Agent step"
    # 的权威落点：现有 agent_steps 只记录步骤名 / 状态 / 失败原因，无法承载
    # "检索到哪些来源、选中了哪些 passage" 这一结构化事实；AgentIntent /
    # AgentInventoryCheck 记录的是其它 Tool，因此本表确属必需。
    #
    # 与 AgentIntent / AgentInventoryCheck 一致：agent_run_id 唯一，每次 Run 至多
    # 一条检索记录；ON DELETE CASCADE 使 demo 数据重置（clear_demo_data 批量删除
    # tickets）保持可重复。passages_json 只承载真实 retrieval result 的
    # rank/score/chunk_key/passage 快照，不伪造 score / token 等不存在的元数据。
    op.create_table(
        "agent_policy_retrievals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("query_summary", sa.Text(), nullable=False),
        sa.Column("document_key", sa.String(length=128), nullable=True),
        sa.Column("document_title", sa.String(length=128), nullable=True),
        sa.Column("source_reference", sa.String(length=256), nullable=True),
        sa.Column("is_demo_data", sa.Boolean(), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("passages_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("agent_run_id"),
    )
    op.create_index(
        op.f("ix_agent_policy_retrievals_agent_run_id"),
        "agent_policy_retrievals",
        ["agent_run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_agent_policy_retrievals_agent_run_id"),
        table_name="agent_policy_retrievals",
    )
    op.drop_table("agent_policy_retrievals")
    # 枚举取值无法简单回退（可能已被其它行使用），因此 downgrade 只移除本迁移新增
    # 的表，不 DROP enum value——避免破坏既有数据。这与"新增取值是向后兼容的增量"
    # 语义一致。
