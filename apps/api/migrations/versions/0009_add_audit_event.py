"""add audit event persistence model

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 与 0001 / 0002 / 0005 / 0006 / 0007 一致：create_type=False 阻止
# op.create_table 内部触发第二次 CREATE TYPE，枚举类型的创建统一由 upgrade() 中
# 显式的 .create(checkfirst=True) 负责。
#
# 两个枚举复用 T023 领域契约中已有的取值，迁移不自行发明取值：
# auditeventtype / actortype 来自 models.py 的 AuditEventType / ActorType。
audit_event_type_enum = PG_ENUM(
    "decision_produced",
    "get_order",
    "check_inventory",
    "risk_gate",
    "approval_request_created",
    "approval_approved",
    "approval_rejected",
    "create_replacement",
    "update_ticket",
    "agent_run_outcome",
    name="auditeventtype",
    create_type=False,
)
actor_type_enum = PG_ENUM(
    "agent",
    "system",
    "human",
    name="actortype",
    create_type=False,
)


def upgrade() -> None:
    audit_event_type_enum.create(op.get_bind(), checkfirst=True)
    actor_type_enum.create(op.get_bind(), checkfirst=True)

    # audit_events 是 T023 "一次已经真实发生的执行事实"的权威落点。现有 domain
    # 中没有任何表能承载它：agent_steps 记录的是步骤痕迹，approval_requests 记录
    # 的是审批对象，都不能表达"谁在什么时候对哪个对象做了什么、结果如何"这一
    # 完整审计事实，因此本迁移确属必需。
    #
    # 核心身份事实全部用明确列表达；metadata_json 只承载少量安全的结构化补充。
    # business_key 的唯一约束是幂等去重的持久化依据：API retry / Resume retry /
    # process restart / 并发 resume / 同一审批重试都无法产生第二条语义相同的事件。
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=128), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("event_type", audit_event_type_enum, nullable=False),
        sa.Column("actor_type", actor_type_enum, nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("affected_object_type", sa.String(length=64), nullable=True),
        sa.Column("affected_object_key", sa.String(length=128), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("reference_type", sa.String(length=32), nullable=True),
        sa.Column("reference_key", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # 外键使"无法追溯 Agent Run 的孤立审计事件"在数据库层不可能存在。
        # ON DELETE CASCADE 与 Run 的其余关联记录一致，使 demo 数据重置可重复。
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(
        op.f("ix_audit_events_business_key"),
        "audit_events",
        ["business_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_audit_events_agent_run_id"),
        "audit_events",
        ["agent_run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_events_agent_run_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_business_key"), table_name="audit_events")
    op.drop_table("audit_events")
    # 显式清理由本迁移创建的枚举类型，保证 downgrade 之后可以重新 upgrade
    sa.Enum(name="actortype").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="auditeventtype").drop(op.get_bind(), checkfirst=True)
