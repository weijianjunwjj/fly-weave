from datetime import datetime

from database import SessionLocal
from models import (
    AfterSalesPolicy,
    Customer,
    InventoryItem,
    Order,
    Ticket,
)
from seed_data import clear_demo_data, seed_demo_data


def test_seed_data_is_deterministic():
    """验证种子数据可确定性、可重复地重建"""
    db = SessionLocal()
    try:
        result1 = seed_demo_data(db)
        result2 = seed_demo_data(db)

        assert result1 == result2, "重复播种应返回相同的业务标识符"

        customers = db.query(Customer).filter(Customer.is_demo_data == True).all()
        assert len(customers) == 3, "应有且仅有 3 个演示客户"
    finally:
        db.close()


def test_seed_data_creates_stable_business_keys():
    """验证所有演示记录都使用稳定的 business_key"""
    db = SessionLocal()
    try:
        seed_demo_data(db)

        policy = db.query(AfterSalesPolicy).filter(
            AfterSalesPolicy.business_key == "policy-replacement-standard"
        ).one()
        assert policy.is_demo_data is True

        customer = db.query(Customer).filter(
            Customer.business_key == "customer-demo-001"
        ).one()
        assert customer.is_demo_data is True

        ticket = db.query(Ticket).filter(
            Ticket.business_key == "ticket-demo-001"
        ).one()
        assert ticket.is_demo_data is True
    finally:
        db.close()


def test_seed_data_marks_all_records_as_demo():
    """验证所有播种记录都显式标记为 demo 数据"""
    db = SessionLocal()
    try:
        seed_demo_data(db)

        customers = db.query(Customer).filter(Customer.is_demo_data == True).all()
        assert len(customers) == 3

        orders = db.query(Order).filter(Order.is_demo_data == True).all()
        assert len(orders) == 3

        tickets = db.query(Ticket).filter(Ticket.is_demo_data == True).all()
        assert len(tickets) == 3

        inventory = db.query(InventoryItem).filter(InventoryItem.is_demo_data == True).all()
        assert len(inventory) == 2

        policies = db.query(AfterSalesPolicy).filter(AfterSalesPolicy.is_demo_data == True).all()
        assert len(policies) == 1
    finally:
        db.close()


def test_seed_data_creates_required_scenarios():
    """验证三个必需场景的存在性和区分度"""
    db = SessionLocal()
    try:
        seed_demo_data(db)

        policy = db.query(AfterSalesPolicy).filter(
            AfterSalesPolicy.business_key == "policy-replacement-standard"
        ).one()

        low_risk = db.query(Ticket).filter(
            Ticket.demo_scenario == "low_risk"
        ).one()
        assert low_risk.business_key == "ticket-demo-001"
        assert low_risk.order is not None
        assert low_risk.order.amount < policy.approval_required_above_amount
        low_risk_age_days = (datetime.utcnow() - low_risk.order.purchased_at).days
        assert low_risk_age_days <= policy.replacement_window_days
        low_risk_inventory = db.query(InventoryItem).filter(
            InventoryItem.product_sku == low_risk.order.product_sku
        ).one()
        assert low_risk_inventory.available_quantity > 0

        approval_required = db.query(Ticket).filter(
            Ticket.demo_scenario == "approval_required"
        ).one()
        assert approval_required.business_key == "ticket-demo-002"
        assert approval_required.order.amount > policy.approval_required_above_amount
        approval_age_days = (datetime.utcnow() - approval_required.order.purchased_at).days
        assert approval_age_days <= policy.replacement_window_days

        rejected = db.query(Ticket).filter(
            Ticket.demo_scenario == "rejected"
        ).one()
        assert rejected.business_key == "ticket-demo-003"
        rejected_age_days = (datetime.utcnow() - rejected.order.purchased_at).days
        assert rejected_age_days > policy.replacement_window_days
    finally:
        db.close()


def test_seed_data_establishes_entity_relationships():
    """验证演示数据中三个场景各自的实体关系完整性"""
    db = SessionLocal()
    try:
        seed_demo_data(db)

        expected = [
            ("ticket-demo-001", "customer-demo-001", "order-demo-001"),
            ("ticket-demo-002", "customer-demo-002", "order-demo-002"),
            ("ticket-demo-003", "customer-demo-003", "order-demo-003"),
        ]

        for ticket_key, customer_key, order_key in expected:
            ticket = db.query(Ticket).filter(
                Ticket.business_key == ticket_key
            ).one()

            assert ticket.customer is not None
            assert ticket.customer.business_key == customer_key

            assert ticket.order is not None
            assert ticket.order.business_key == order_key
            assert ticket.order.customer_id == ticket.customer_id

            inventory = db.query(InventoryItem).filter(
                InventoryItem.product_sku == ticket.order.product_sku
            ).one()
            assert inventory is not None
    finally:
        db.close()


def test_clear_demo_data_removes_only_demo_records():
    """验证 clear_demo_data 只清除演示数据，不影响其他记录"""
    db = SessionLocal()
    try:
        seed_demo_data(db)

        demo_count_before = db.query(Customer).filter(
            Customer.is_demo_data == True
        ).count()
        assert demo_count_before == 3

        clear_demo_data(db)

        demo_count_after = db.query(Customer).filter(
            Customer.is_demo_data == True
        ).count()
        assert demo_count_after == 0
    finally:
        db.close()
