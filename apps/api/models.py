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
    UniqueConstraint,
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


class AgentRunStatus(PyEnum):
    """Agent Run 状态。waiting_for_approval 使一次 Run 能够跨人工审批暂停并恢复"""
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentStepStatus(PyEnum):
    """Agent Run 内单个步骤的状态"""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


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
    agent_runs = relationship("AgentRun", back_populates="ticket")


class AgentRun(Base):
    """
    一次 Agent 执行的持久化状态。

    Run 被建模为独立于 HTTP 请求生命周期的应用状态：状态、时间戳与失败原因
    全部落库，因此一次 Run 可以在请求结束后继续存在、跨人工审批暂停并恢复，
    失败的 Run 也会持续可见为 failed。

    本模型只描述执行状态，不包含任何执行、调度或工具调用行为。
    """
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 与其它领域模型一致的稳定业务标识符，供 API / UI 寻址某次 Run，
    # 避免对外暴露自增主键
    business_key = Column(String(64), unique=True, nullable=False, index=True)
    # 一次 Run 始终归属于一个工单。ON DELETE CASCADE 保证 demo 数据重置
    # （clear_demo_data 直接批量删除 tickets）保持可重复，不会因残留 Run 失败
    ticket_id = Column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status = Column(
        Enum(AgentRunStatus, name="agentrunstatus", values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
        default=AgentRunStatus.QUEUED,
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # 尚未开始 / 尚未结束的 Run 保持为 NULL，不用占位时间伪造执行事实
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    # 失败原因持久化，使失败的 Run 在事后仍可解释
    error_message = Column(Text, nullable=True)

    ticket = relationship("Ticket", back_populates="agent_runs")
    steps = relationship(
        "AgentStep",
        back_populates="agent_run",
        order_by="AgentStep.step_order",
        cascade="all, delete-orphan",
    )


class AgentStep(Base):
    """
    Agent Run 中的单个步骤记录。

    步骤通过显式的 step_order 排序，使时间线查询结果确定、不依赖插入顺序或
    时间戳精度。步骤只记录名称与状态，工具调用、模型输出与 trace 数据不在
    本任务范围内。
    """
    __tablename__ = "agent_steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_run_id = Column(
        Integer, ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 同一次 Run 内的步骤序号，从 1 开始，保证时间线顺序确定
    step_order = Column(Integer, nullable=False)
    name = Column(String(128), nullable=False)
    status = Column(
        Enum(AgentStepStatus, name="agentstepstatus", values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
        default=AgentStepStatus.PENDING,
    )
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    agent_run = relationship("AgentRun", back_populates="steps")

    __table_args__ = (
        UniqueConstraint("agent_run_id", "step_order", name="uq_agent_steps_run_step_order"),
    )
