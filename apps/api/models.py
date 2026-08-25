from datetime import datetime
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
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


class TicketResolution(PyEnum):
    """工单解决结果（T017）。

    当前只有 ``REPLACEMENT_CREATED`` 这一个真实取值：Golden Path 上唯一已经
    存在的解决方式就是"换货单已被真实创建"。退款、补发、话术安抚等结果在当前
    domain 中没有任何真实执行路径可以产生它们，因此不预先编造。
    """
    REPLACEMENT_CREATED = "replacement_created"


class ReplacementStatus(PyEnum):
    """换货单状态。

    当前只存在 ``CREATED`` 这一个真实状态：T016 只负责"换货单已被真实创建"
    这一业务事实。发货、取消、完成等后续状态在当前 domain 中尚不存在真实的
    状态迁移来源，因此不预先编造。
    """
    CREATED = "created"


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

    # --- T017：update_ticket 回写的解决结果 ---
    # 四个字段共同表示"这张工单已被一次真实执行解决"。未解决的工单一律保持
    # NULL，不用占位值伪造解决事实。
    resolution = Column(
        Enum(TicketResolution, name="ticketresolution", values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=True,
    )
    resolution_summary = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    # 解决结果所引用的那张真实换货单。使用外键而非文本标识，使"引用一张不存在
    # 的换货单"在数据库层就无法成立；唯一约束保证一张换货单至多被一张工单当作
    # 解决结果。ON DELETE SET NULL：换货单若被清理，工单不会跟着消失，但也绝不
    # 保留一个悬空引用。
    replacement_id = Column(
        Integer,
        ForeignKey(
            "replacement_orders.id",
            # 约束名与 0006 迁移中显式创建的名称保持一致；use_alter 使 tickets 与
            # replacement_orders 之间的互相引用能够以独立 ALTER 表达
            name="fk_tickets_replacement_id_replacement_orders",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
        unique=True,
        index=True,
    )

    customer = relationship("Customer", back_populates="tickets")
    order = relationship("Order", back_populates="tickets")
    agent_runs = relationship("AgentRun", back_populates="ticket")
    # tickets 与 replacement_orders 互相引用，因此两侧都显式指定 foreign_keys；
    # post_update 让 SQLAlchemy 以独立 UPDATE 写入本列，避免循环依赖下的 flush 排序问题
    resolution_replacement = relationship(
        "ReplacementOrder",
        foreign_keys=[replacement_id],
        post_update=True,
    )


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
    # T011：一次 Run 至多关联一条已验证的结构化 intent
    intent = relationship(
        "AgentIntent",
        back_populates="agent_run",
        uselist=False,
        cascade="all, delete-orphan",
    )
    # T014：一次 Run 至多关联一条 check_inventory 的实际调用记录
    inventory_check = relationship(
        "AgentInventoryCheck",
        back_populates="agent_run",
        uselist=False,
        cascade="all, delete-orphan",
    )
    # T016：一次 Run 至多执行一次换货这一受保护的业务变更
    replacement = relationship(
        "ReplacementOrder",
        back_populates="agent_run",
        uselist=False,
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


class AgentIntent(Base):
    """
    已验证的结构化 intent 持久化记录。

    这是 T011 的 intent 抽取结果，以类型化字段（而非自由 JSON）落库，关联到
    产生它的 AgentRun，可事后查询。只有通过 validation boundary 的成功 intent
    才会被写入本表；失败只体现在 AgentStep 的 failed 状态与结构化失败原因中。
    """
    __tablename__ = "agent_intents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_run_id = Column(
        Integer,
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    intent_type = Column(String(64), nullable=False)
    issue_summary = Column(Text, nullable=False)
    requested_action = Column(String(64), nullable=False)
    confidence = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    agent_run = relationship("AgentRun", back_populates="intent")


class AgentInventoryCheck(Base):
    """
    check_inventory 的实际调用记录（T014）。

    与 AgentIntent 同一思路：把一次真实 Tool 调用的结果以类型化字段（而非
    自由 JSON）落库，关联到产生它的 AgentRun。只有真实执行的库存查询才会被
    写入本表，记录的内容全部来自 ``InventoryCheckResult``：

    - ``status``：success / unavailable / sku_not_found / invalid_request；
    - ``available_quantity`` / ``warehouse`` / ``is_demo_data``：查询到的真实
      库存事实；查无 SKU 或非法输入时保持 NULL，不伪造可用性；
    - ``failure_reason``：失败路径的结构化原因，成功 / 无货时为 NULL。

    每次 Run 至多一条（agent_run_id 唯一），并随 Run 的删除一并清理。
    """
    __tablename__ = "agent_inventory_checks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_run_id = Column(
        Integer,
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    requested_sku = Column(String(64), nullable=True)
    status = Column(String(32), nullable=False)
    available_quantity = Column(Integer, nullable=True)
    warehouse = Column(String(64), nullable=True)
    is_demo_data = Column(Boolean, nullable=True)
    failure_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    agent_run = relationship("AgentRun", back_populates="inventory_check")


class ReplacementOrder(Base):
    """
    换货单：Golden Path 上第一个真实的业务状态变更结果（T016）。

    与 AgentIntent / AgentInventoryCheck 这类"Agent 执行痕迹"不同，换货单是
    真正的业务对象：它的存在本身就是"换货已经发生"的权威事实。模型文本无法
    创建它，只有 ``replacement_service`` 在校验全部前置条件后写入。

    三条业务关联全部落库，保持现有 domain 已要求的关系完整：

    - ``order_id``：被换货的真实订单；
    - ``ticket_id``：发起该换货的客服工单；
    - ``agent_run_id``：执行该换货的那次 Agent Run。

    重复执行由持久化状态而非进程内状态阻止：``order_id`` 唯一保证一个订单至多
    一张换货单，``agent_run_id`` 唯一保证一次 Run 至多执行一次该受保护动作。
    这两个约束在数据库层生效，因此并发或重放都无法绕过。

    三个外键都使用 ON DELETE CASCADE，与 AgentRun 一致，使 demo 数据重置
    （clear_demo_data 批量删除 tickets / orders）保持可重复。
    """
    __tablename__ = "replacement_orders"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 由订单业务标识确定性派生（replacement-<order_key>），因此同一订单的换货单
    # 标识稳定可预期。列宽 = 固定前缀 12 + Order.business_key 列宽 64
    business_key = Column(String(80), unique=True, nullable=False, index=True)
    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    ticket_id = Column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id = Column(
        Integer,
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    product_sku = Column(String(64), nullable=False)
    # 换货原因来自已验证 intent 的问题摘要，经由 typed 请求传入
    reason = Column(Text, nullable=False)
    status = Column(
        Enum(ReplacementStatus, name="replacementstatus", values_callable=lambda enum_cls: [member.value for member in enum_cls]),
        nullable=False,
        default=ReplacementStatus.CREATED,
    )
    # 与订单保持一致的演示数据标记，避免模拟换货与生产换货混淆
    is_demo_data = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    order = relationship("Order")
    # 与 Ticket.resolution_replacement 构成两个方向的引用，显式指定本侧外键消歧
    ticket = relationship("Ticket", foreign_keys=[ticket_id])
    agent_run = relationship("AgentRun", back_populates="replacement")
