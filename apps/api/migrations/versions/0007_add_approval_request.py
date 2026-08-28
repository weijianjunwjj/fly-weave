"""add approval request persistence model

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 与 0001 / 0002 / 0005 / 0006 一致：create_type=False 阻止 op.create_table 内部
# 触发第二次 CREATE TYPE，枚举类型的创建统一由 upgrade() 中显式的
# .create(checkfirst=True) 负责。
#
# 四个枚举全部复用 T019 / T020 领域契约中已有的取值，迁移不自行发明取值：
# protectedaction / risklevel / riskrulecode 来自 risk.py，
# approvalrequeststatus 来自 approvals.py。
protected_action_enum = PG_ENUM(
    "create_replacement",
    name="protectedaction",
    create_type=False,
)
approval_request_status_enum = PG_ENUM(
    "pending",
    "approved",
    "rejected",
    name="approvalrequeststatus",
    create_type=False,
)
risk_level_enum = PG_ENUM(
    "low",
    "high",
    name="risklevel",
    create_type=False,
)
risk_rule_code_enum = PG_ENUM(
    "no_rule_triggered",
    "order_amount_above_approval_threshold",
    name="riskrulecode",
    create_type=False,
)

PENDING_UNIQUE_INDEX_NAME = "uq_approval_requests_pending_run_action"
PENDING_NOT_RESOLVED_CHECK_NAME = "ck_approval_requests_pending_not_resolved"


def upgrade() -> None:
    protected_action_enum.create(op.get_bind(), checkfirst=True)
    approval_request_status_enum.create(op.get_bind(), checkfirst=True)
    risk_level_enum.create(op.get_bind(), checkfirst=True)
    risk_rule_code_enum.create(op.get_bind(), checkfirst=True)

    # approval_requests 是 T020 "受保护动作正在等待人工审批"这一事实的权威落点。
    # 现有 domain 中没有任何表能承载它：agent_steps 只能记录执行痕迹与一段
    # error_message，而 pending approval 既不是失败也不是完成，因此本迁移确属必需。
    #
    # 九个 risk_* / reason 列是风险规则触发那一刻的 snapshot，不是可重算的视图：
    # 售后政策阈值日后被修改，也不会改写一条历史审批请求的拦截原因。
    op.create_table(
        "approval_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=128), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("protected_action", protected_action_enum, nullable=False),
        sa.Column("status", approval_request_status_enum, nullable=False),
        sa.Column("risk_level", risk_level_enum, nullable=False),
        sa.Column("risk_rule_code", risk_rule_code_enum, nullable=False),
        sa.Column("risk_requires_approval", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("risk_order_key", sa.String(length=64), nullable=True),
        sa.Column("risk_order_amount", sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column(
            "risk_approval_threshold_amount",
            sa.Numeric(precision=10, scale=2),
            nullable=True,
        ),
        sa.Column("risk_policy_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        # 外键使"无法追溯 Agent Run 的孤立审批请求"在数据库层不可能存在。
        # ON DELETE CASCADE 与 Run 的其余关联记录一致，使 demo 数据重置可重复。
        sa.ForeignKeyConstraint(
            ["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
        # pending 意味着审批尚未有结果，因此绝不能带审批完成时间。
        sa.CheckConstraint(
            "status <> 'pending' OR resolved_at IS NULL",
            name=PENDING_NOT_RESOLVED_CHECK_NAME,
        ),
    )
    op.create_index(
        op.f("ix_approval_requests_business_key"),
        "approval_requests",
        ["business_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_approval_requests_agent_run_id"),
        "approval_requests",
        ["agent_run_id"],
        unique=False,
    )
    # 数据库级防重复：同一次 Run 的同一个受保护动作至多一条 pending 审批请求。
    # 这是 partial index，只约束 pending 行，因此并发重复调用无法产生第二条待审批
    # 记录，而日后 approve / reject 之后仍可再次进入审批。
    op.create_index(
        PENDING_UNIQUE_INDEX_NAME,
        "approval_requests",
        ["agent_run_id", "protected_action"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index(PENDING_UNIQUE_INDEX_NAME, table_name="approval_requests")
    op.drop_index(
        op.f("ix_approval_requests_agent_run_id"), table_name="approval_requests"
    )
    op.drop_index(
        op.f("ix_approval_requests_business_key"), table_name="approval_requests"
    )
    op.drop_table("approval_requests")
    # 显式清理由本迁移创建的枚举类型，保证 downgrade 之后可以重新 upgrade
    sa.Enum(name="riskrulecode").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="risklevel").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="approvalrequeststatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="protectedaction").drop(op.get_bind(), checkfirst=True)
