"""add replacement order persistence model

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 与 0001 / 0002 一致：create_type=False 阻止 op.create_table 内部触发第二次
# CREATE TYPE，枚举类型的创建统一由 upgrade() 中显式的 .create(checkfirst=True) 负责
replacement_status_enum = PG_ENUM(
    "created",
    name="replacementstatus",
    create_type=False,
)


def upgrade() -> None:
    replacement_status_enum.create(op.get_bind(), checkfirst=True)

    # replacement_orders 是 T016 换货执行结果的权威落点。现有 domain 中没有任何
    # 表能承载一张真实换货单（agent_intents / agent_inventory_checks 记录的是
    # Agent 执行痕迹，不是业务对象），因此本迁移确属必需。
    #
    # 三个外键都使用 ON DELETE CASCADE，与 agent_runs 一致，使 demo 数据重置
    # （clear_demo_data 批量删除 tickets / orders）保持可重复。
    # order_id 与 agent_run_id 的唯一约束使"重复执行"在数据库层被拒绝：
    # 一个订单至多一张换货单，一次 Run 至多执行一次该受保护动作。
    op.create_table(
        "replacement_orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=80), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=False),
        sa.Column("agent_run_id", sa.Integer(), nullable=False),
        sa.Column("product_sku", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", replacement_status_enum, nullable=False),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
        sa.UniqueConstraint("order_id"),
        sa.UniqueConstraint("agent_run_id"),
    )
    op.create_index(
        op.f("ix_replacement_orders_business_key"),
        "replacement_orders",
        ["business_key"],
        unique=True,
    )
    op.create_index(
        op.f("ix_replacement_orders_order_id"),
        "replacement_orders",
        ["order_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_replacement_orders_ticket_id"),
        "replacement_orders",
        ["ticket_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_replacement_orders_agent_run_id"),
        "replacement_orders",
        ["agent_run_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_replacement_orders_agent_run_id"), table_name="replacement_orders"
    )
    op.drop_index(
        op.f("ix_replacement_orders_ticket_id"), table_name="replacement_orders"
    )
    op.drop_index(
        op.f("ix_replacement_orders_order_id"), table_name="replacement_orders"
    )
    op.drop_index(
        op.f("ix_replacement_orders_business_key"), table_name="replacement_orders"
    )
    op.drop_table("replacement_orders")
    # 显式清理由本迁移创建的枚举类型，保证 downgrade 之后可以重新 upgrade
    sa.Enum(name="replacementstatus").drop(op.get_bind(), checkfirst=True)
