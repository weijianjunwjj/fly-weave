"""add ticket resolution write-back columns

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 与 0001 / 0002 / 0005 一致：create_type=False 阻止 add_column 内部触发第二次
# CREATE TYPE，枚举类型的创建统一由 upgrade() 中显式的 .create(checkfirst=True) 负责
ticket_resolution_enum = PG_ENUM(
    "replacement_created",
    name="ticketresolution",
    create_type=False,
)

REPLACEMENT_FK_NAME = "fk_tickets_replacement_id_replacement_orders"


def upgrade() -> None:
    ticket_resolution_enum.create(op.get_bind(), checkfirst=True)

    # T017 的工单回写需要在工单侧留下四条真实痕迹：解决结果、结果摘要、解决时刻，
    # 以及所引用的那张真实换货单。现有 tickets 表只有 status，无法承载"引用了哪
    # 一张换货单"，因此本迁移确属必需。
    #
    # 四列全部 nullable：未被解决的工单保持 NULL，不用占位值伪造解决事实。
    op.add_column("tickets", sa.Column("resolution", ticket_resolution_enum, nullable=True))
    op.add_column("tickets", sa.Column("resolution_summary", sa.Text(), nullable=True))
    op.add_column("tickets", sa.Column("resolved_at", sa.DateTime(), nullable=True))
    op.add_column("tickets", sa.Column("replacement_id", sa.Integer(), nullable=True))

    # 唯一索引：一张换货单至多被一张工单当作解决结果。
    op.create_index(
        op.f("ix_tickets_replacement_id"), "tickets", ["replacement_id"], unique=True
    )
    # 外键让"引用一张不存在的换货单"在数据库层不可能成立。
    # ON DELETE SET NULL：换货单被清理时工单保留，但不会留下悬空引用。
    # replacement_orders.ticket_id 反向指向 tickets 且为 ON DELETE CASCADE，
    # 因此 clear_demo_data 批量删除工单时，级联删除换货单再把本列置空，仍然可重复。
    op.create_foreign_key(
        REPLACEMENT_FK_NAME,
        "tickets",
        "replacement_orders",
        ["replacement_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(REPLACEMENT_FK_NAME, "tickets", type_="foreignkey")
    op.drop_index(op.f("ix_tickets_replacement_id"), table_name="tickets")
    op.drop_column("tickets", "replacement_id")
    op.drop_column("tickets", "resolved_at")
    op.drop_column("tickets", "resolution_summary")
    op.drop_column("tickets", "resolution")
    # 显式清理由本迁移创建的枚举类型，保证 downgrade 之后可以重新 upgrade
    sa.Enum(name="ticketresolution").drop(op.get_bind(), checkfirst=True)
