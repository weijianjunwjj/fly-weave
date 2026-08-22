from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from database import Base


class TicketStatus(PyEnum):
    """工单状态"""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    RESOLVED = "resolved"
    CLOSED = "closed"


class OrderStatus(PyEnum):
    """订单状态"""
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Customer(Base):
    """客户 / 客户引用信息"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 稳定的业务标识符，用于跨环境重复播种时保持幂等
    business_key = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(128), nullable=False)
    email = Column(String(255), nullable=False)
    phone = Column(String(32), nullable=True)
    # 显式标记该记录是否为演示 / 模拟数据，避免与生产数据混淆
    is_demo_data = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    tickets = relationship("Ticket", back_populates="customer")
    orders = relationship("Order", back_populates="customer")


class Order(Base):
    """订单"""
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_key = Column(String(64), unique=True, nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    product_sku = Column(String(64), nullable=False)
    product_name = Column(String(128), nullable=False)
    purchased_at = Column(DateTime, nullable=False)
    status = Column(
        Enum(OrderStatus, name="orderstatus", values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
        default=OrderStatus.DELIVERED,
    )
    amount = Column(Numeric(10, 2), nullable=False)
    is_demo_data = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="orders")
    tickets = relationship("Ticket", back_populates="order")


class InventoryItem(Base):
    """库存 / 换货可用性"""
    __tablename__ = "inventory_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_key = Column(String(64), unique=True, nullable=False, index=True)
    product_sku = Column(String(64), unique=True, nullable=False, index=True)
    product_name = Column(String(128), nullable=False)
    available_quantity = Column(Integer, nullable=False, default=0)
    warehouse = Column(String(64), nullable=False)
    is_demo_data = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AfterSalesPolicy(Base):
    """售后政策"""
    __tablename__ = "after_sales_policies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_key = Column(String(64), unique=True, nullable=False, index=True)
    title = Column(String(128), nullable=False)
    content = Column(Text, nullable=False)
    # 换货允许的最长天数，供确定性业务规则引用
    replacement_window_days = Column(Integer, nullable=False)
    # 超过该金额的订单需要人工审批
    approval_required_above_amount = Column(Numeric(10, 2), nullable=True)
    source_reference = Column(String(128), nullable=False)
    is_demo_data = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class Ticket(Base):
    """客服工单"""
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    business_key = Column(String(64), unique=True, nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)
    subject = Column(String(128), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(
        Enum(TicketStatus, name="ticketstatus", values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
        default=TicketStatus.OPEN,
    )
    # 场景标签，用于区分演示脚本中的低风险 / 需审批 / 拒绝场景
    demo_scenario = Column(String(32), nullable=True)
    is_demo_data = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="tickets")
    order = relationship("Order", back_populates="tickets")
