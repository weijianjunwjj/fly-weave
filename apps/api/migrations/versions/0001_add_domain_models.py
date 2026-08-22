"""add customer, order, inventory, ticket, after-sales policy domain models

Revision ID: 0001
Revises:
Create Date: 2026-08-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# create_type=False 阻止这些枚举对象在被用作列类型时（op.create_table 内部）
# 触发隐式的第二次 CREATE TYPE。枚举类型的创建统一由 upgrade() 中的
# 显式 .create(checkfirst=True) 调用负责，形成唯一权威的创建路径。
order_status_enum = PG_ENUM(
    "pending", "paid", "shipped", "delivered", "cancelled",
    name="orderstatus",
    create_type=False,
)
ticket_status_enum = PG_ENUM(
    "open", "in_progress", "waiting_for_approval", "resolved", "closed",
    name="ticketstatus",
    create_type=False,
)


def upgrade() -> None:
    # 显式创建 PostgreSQL ENUM 类型，checkfirst=True 使其幂等（类型已存在时跳过）
    order_status_enum.create(op.get_bind(), checkfirst=True)
    ticket_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(op.f("ix_customers_business_key"), "customers", ["business_key"], unique=True)

    op.create_table(
        "inventory_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=64), nullable=False),
        sa.Column("product_sku", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=128), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("warehouse", sa.String(length=64), nullable=False),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
        sa.UniqueConstraint("product_sku"),
    )
    op.create_index(op.f("ix_inventory_items_business_key"), "inventory_items", ["business_key"], unique=True)
    op.create_index(op.f("ix_inventory_items_product_sku"), "inventory_items", ["product_sku"], unique=True)

    op.create_table(
        "after_sales_policies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("replacement_window_days", sa.Integer(), nullable=False),
        sa.Column("approval_required_above_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("source_reference", sa.String(length=128), nullable=False),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(op.f("ix_after_sales_policies_business_key"), "after_sales_policies", ["business_key"], unique=True)

    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("product_sku", sa.String(length=64), nullable=False),
        sa.Column("product_name", sa.String(length=128), nullable=False),
        sa.Column("purchased_at", sa.DateTime(), nullable=False),
        sa.Column("status", order_status_enum, nullable=False),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(op.f("ix_orders_business_key"), "orders", ["business_key"], unique=True)

    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("business_key", sa.String(length=64), nullable=False),
        sa.Column("customer_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", ticket_status_enum, nullable=False),
        sa.Column("demo_scenario", sa.String(length=32), nullable=True),
        sa.Column("is_demo_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(op.f("ix_tickets_business_key"), "tickets", ["business_key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_tickets_business_key"), table_name="tickets")
    op.drop_table("tickets")
    op.drop_index(op.f("ix_orders_business_key"), table_name="orders")
    op.drop_table("orders")
    op.drop_index(op.f("ix_after_sales_policies_business_key"), table_name="after_sales_policies")
    op.drop_table("after_sales_policies")
    op.drop_index(op.f("ix_inventory_items_product_sku"), table_name="inventory_items")
    op.drop_index(op.f("ix_inventory_items_business_key"), table_name="inventory_items")
    op.drop_table("inventory_items")
    op.drop_index(op.f("ix_customers_business_key"), table_name="customers")
    op.drop_table("customers")
    ticket_status_enum.drop(op.get_bind(), checkfirst=True)
    order_status_enum.drop(op.get_bind(), checkfirst=True)
