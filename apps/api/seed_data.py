from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.orm import Session

from database import SessionLocal
from demo_policy_source import DEMO_REPLACEMENT_POLICY_DOCUMENT
from models import (
    AfterSalesPolicy,
    Customer,
    InventoryItem,
    Order,
    OrderStatus,
    PolicyChunk,
    PolicyDocument,
    Ticket,
    TicketStatus,
)
from policy_ingestion_service import ingest_policy_document


def clear_demo_data(db: Session) -> None:
    """清除所有标记为 demo 的数据，保证幂等性"""
    db.query(Ticket).filter(Ticket.is_demo_data == True).delete()
    db.query(Order).filter(Order.is_demo_data == True).delete()
    db.query(Customer).filter(Customer.is_demo_data == True).delete()
    db.query(InventoryItem).filter(InventoryItem.is_demo_data == True).delete()
    db.query(AfterSalesPolicy).filter(AfterSalesPolicy.is_demo_data == True).delete()
    # T024：policy knowledge 层。先删 chunks 再删 documents（虽然后者有 ON DELETE
    # CASCADE，这里仍显式清理以保证删除顺序确定、不遗留阻塞下次 seed 的脏数据）。
    db.query(PolicyChunk).filter(PolicyChunk.is_demo_data == True).delete()
    db.query(PolicyDocument).filter(PolicyDocument.is_demo_data == True).delete()
    db.commit()


def seed_demo_data(db: Session) -> dict:
    """
    创建确定性、可重复的演示数据集。

    包含三个核心场景：
    1. low_risk: 低风险换货，金额低、时间窗口内、有库存
    2. approval_required: 需人工审批，金额超过阈值
    3. rejected: 拒绝场景，超出换货时间窗口，策略明确不允许

    所有业务标识符（business_key）都是稳定的固定字符串，重复运行本函数
    会先清空现有 demo 数据再重建，因此可反复产生同一份规范演示数据集。
    """
    clear_demo_data(db)

    # T024：把可摄取的政策知识源材料通过 deterministic ingestion 落库为
    # PolicyDocument + PolicyChunk，作为后续 retrieval corpus 的稳定基线。
    ingestion_result = ingest_policy_document(db, DEMO_REPLACEMENT_POLICY_DOCUMENT)

    policy = AfterSalesPolicy(
        business_key="policy-replacement-standard",
        title="标准换货政策",
        content=(
            "产品在购买后 30 天内出现质量问题，且经确认为非人为损坏，"
            "可申请免费换货。订单金额超过 500 元的换货需要人工审批。"
            "超过 30 天窗口期的换货请求将被拒绝。"
        ),
        replacement_window_days=30,
        approval_required_above_amount=Decimal("500.00"),
        source_reference="policy-doc://after-sales/v1#replacement",
        is_demo_data=True,
    )
    db.add(policy)

    customer_low_risk = Customer(
        business_key="customer-demo-001",
        name="陈晓明",
        email="demo.chenxiaoming@example.com",
        phone="+86-138-0000-0001",
        is_demo_data=True,
    )
    customer_approval = Customer(
        business_key="customer-demo-002",
        name="李文静",
        email="demo.liwenjing@example.com",
        phone="+86-138-0000-0002",
        is_demo_data=True,
    )
    customer_rejected = Customer(
        business_key="customer-demo-003",
        name="王大力",
        email="demo.wangdali@example.com",
        phone="+86-138-0000-0003",
        is_demo_data=True,
    )
    db.add_all([customer_low_risk, customer_approval, customer_rejected])
    db.flush()

    inventory_earbuds = InventoryItem(
        business_key="inventory-demo-earbuds",
        product_sku="SKU-EARBUD-PRO-01",
        product_name="Flyweave 无线耳机 Pro",
        available_quantity=12,
        warehouse="WH-EAST-01",
        is_demo_data=True,
    )
    inventory_headset = InventoryItem(
        business_key="inventory-demo-headset",
        product_sku="SKU-HEADSET-X-02",
        product_name="Flyweave 头戴式耳机 X",
        available_quantity=0,
        warehouse="WH-EAST-01",
        is_demo_data=True,
    )
    db.add_all([inventory_earbuds, inventory_headset])

    # 锚定于播种执行时刻的相对时间，保证业务规则（30 天窗口）在任意运行时刻都成立
    now = datetime.utcnow()

    order_low_risk = Order(
        business_key="order-demo-001",
        customer_id=customer_low_risk.id,
        product_sku=inventory_earbuds.product_sku,
        product_name=inventory_earbuds.product_name,
        purchased_at=now - timedelta(days=5),
        status=OrderStatus.DELIVERED,
        amount=Decimal("299.00"),
        is_demo_data=True,
    )
    order_approval = Order(
        business_key="order-demo-002",
        customer_id=customer_approval.id,
        product_sku=inventory_earbuds.product_sku,
        product_name=inventory_earbuds.product_name,
        purchased_at=now - timedelta(days=10),
        status=OrderStatus.DELIVERED,
        amount=Decimal("1299.00"),
        is_demo_data=True,
    )
    order_rejected = Order(
        business_key="order-demo-003",
        customer_id=customer_rejected.id,
        product_sku=inventory_headset.product_sku,
        product_name=inventory_headset.product_name,
        purchased_at=now - timedelta(days=60),
        status=OrderStatus.DELIVERED,
        amount=Decimal("399.00"),
        is_demo_data=True,
    )
    db.add_all([order_low_risk, order_approval, order_rejected])
    db.flush()

    ticket_low_risk = Ticket(
        business_key="ticket-demo-001",
        customer_id=customer_low_risk.id,
        order_id=order_low_risk.id,
        subject="右耳耳机无声，申请换货",
        description="客户反馈右耳耳机完全没有声音，购买不久，怀疑质量问题，要求换货。",
        status=TicketStatus.OPEN,
        demo_scenario="low_risk",
        is_demo_data=True,
    )
    ticket_approval = Ticket(
        business_key="ticket-demo-002",
        customer_id=customer_approval.id,
        order_id=order_approval.id,
        subject="耳机进水损坏，申请换货",
        description="客户反馈耳机进水后无法正常使用，订单金额较高，需人工审批后才能换货。",
        status=TicketStatus.OPEN,
        demo_scenario="approval_required",
        is_demo_data=True,
    )
    ticket_rejected = Ticket(
        business_key="ticket-demo-003",
        customer_id=customer_rejected.id,
        order_id=order_rejected.id,
        subject="头戴耳机头带断裂，申请换货",
        description="客户购买已超过 60 天，头带断裂申请换货，超出售后换货时间窗口。",
        status=TicketStatus.OPEN,
        demo_scenario="rejected",
        is_demo_data=True,
    )
    db.add_all([ticket_low_risk, ticket_approval, ticket_rejected])
    db.commit()

    return {
        "policy_business_key": policy.business_key,
        "policy_document_key": ingestion_result.document_key,
        "policy_document_chunk_count": ingestion_result.chunk_count,
        "low_risk_ticket": ticket_low_risk.business_key,
        "approval_required_ticket": ticket_approval.business_key,
        "rejected_ticket": ticket_rejected.business_key,
    }


def main() -> None:
    """CLI 入口：执行确定性演示数据播种"""
    db = SessionLocal()
    try:
        result = seed_demo_data(db)
        print("演示数据播种完成:")
        for key, value in result.items():
            print(f"  {key}: {value}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
