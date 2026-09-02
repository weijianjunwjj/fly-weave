"""Service Operations 的正式工单受理边界。

本服务只负责把一次已验证的受理请求原子持久化为 Customer、Order 与 Ticket。
后续 AI 处理仍由既有 run_golden_path 驱动，不在这里复制执行或审批逻辑。
"""
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import Customer, InventoryItem, Order, OrderStatus, Ticket, TicketStatus


class DuplicateOrderError(Exception):
    """用户提供的订单业务编号已存在。"""


class IntakeProductUnavailableError(Exception):
    """当前没有可供既有换货流程核验的真实库存商品。"""


def create_ticket(
    db: Session,
    *,
    customer_name: str,
    customer_email: str,
    issue_type: str,
    issue_description: str,
    order_key: str,
    order_amount: Decimal,
    is_demo_data: bool = False,
    demo_scenario: str | None = None,
) -> Ticket:
    """原子创建一张正式售后工单及其客户、订单上下文。"""

    if db.query(Order.id).filter(Order.business_key == order_key).first() is not None:
        raise DuplicateOrderError(f"订单编号已存在: {order_key}")

    # 既有 Golden Path 会按订单 SKU 查询真实库存；选择当前确有库存的一项，避免
    # 为 Intake 伪造商品或另建库存路径。产品 taxonomy/选择器不属于本任务范围。
    inventory = (
        db.query(InventoryItem)
        .filter(InventoryItem.available_quantity > 0)
        .order_by(InventoryItem.id.asc())
        .first()
    )
    if inventory is None:
        raise IntakeProductUnavailableError("当前没有可用于售后换货处理的在库商品")

    suffix = uuid4().hex
    now = datetime.utcnow()
    customer = Customer(
        business_key=f"customer-{suffix}",
        name=customer_name.strip(),
        email=customer_email.strip(),
        phone=None,
        is_demo_data=is_demo_data,
    )
    db.add(customer)
    db.flush()

    order = Order(
        business_key=order_key.strip(),
        customer_id=customer.id,
        product_sku=inventory.product_sku,
        product_name=inventory.product_name,
        purchased_at=now,
        status=OrderStatus.DELIVERED,
        amount=order_amount,
        is_demo_data=is_demo_data,
    )
    db.add(order)
    db.flush()

    ticket = Ticket(
        business_key=f"ticket-{suffix}",
        customer_id=customer.id,
        order_id=order.id,
        subject=f"{issue_type.strip()}售后申请",
        issue_type=issue_type.strip(),
        description=issue_description.strip(),
        status=TicketStatus.OPEN,
        demo_scenario=demo_scenario,
        is_demo_data=is_demo_data,
        created_at=now,
        updated_at=now,
    )
    db.add(ticket)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise DuplicateOrderError(f"订单编号已存在: {order_key}") from exc

    db.refresh(ticket)
    return ticket
