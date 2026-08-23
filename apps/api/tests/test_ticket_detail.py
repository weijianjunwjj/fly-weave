from fastapi.testclient import TestClient

from database import SessionLocal
from main import app
from seed_data import seed_demo_data


client = TestClient(app)


def seed() -> None:
    """重建确定性演示数据，保证详情断言基于真实持久化记录"""
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


def test_ticket_detail_returns_persisted_seeded_ticket():
    """验证详情端点能按业务标识返回已持久化的演示工单本体字段"""
    seed()

    response = client.get("/tickets/ticket-demo-001")
    assert response.status_code == 200

    detail = response.json()
    assert detail["business_key"] == "ticket-demo-001"
    assert detail["subject"] == "右耳耳机无声，申请换货"
    assert detail["description"]
    assert detail["status"] == "open"
    assert detail["demo_scenario"] == "low_risk"
    assert detail["is_demo_data"] is True


def test_ticket_detail_includes_related_customer_and_order_context():
    """验证详情端点通过既有关系返回关联的客户与订单 / 商品事实"""
    seed()

    response = client.get("/tickets/ticket-demo-001")
    assert response.status_code == 200

    detail = response.json()

    customer = detail["customer"]
    assert customer is not None
    assert customer["business_key"] == "customer-demo-001"
    assert customer["name"] == "陈晓明"
    assert customer["email"] == "demo.chenxiaoming@example.com"
    assert customer["is_demo_data"] is True

    order = detail["order"]
    assert order is not None
    assert order["business_key"] == "order-demo-001"
    assert order["product_sku"] == "SKU-EARBUD-PRO-01"
    assert order["product_name"] == "Flyweave 无线耳机 Pro"
    assert order["status"] == "delivered"
    assert order["amount"] == "299.00"
    assert order["purchased_at"]
    assert order["is_demo_data"] is True


def test_ticket_detail_returns_approval_scenario_context():
    """验证另一个演示场景同样基于持久化数据返回，而不是固定单例"""
    seed()

    response = client.get("/tickets/ticket-demo-002")
    assert response.status_code == 200

    detail = response.json()
    assert detail["demo_scenario"] == "approval_required"
    assert detail["customer"]["business_key"] == "customer-demo-002"
    assert detail["order"]["business_key"] == "order-demo-002"
    assert detail["order"]["amount"] == "1299.00"


def test_ticket_detail_unknown_business_key_returns_not_found():
    """验证未知业务标识返回诚实的 404，而不是回退或合成数据"""
    seed()

    response = client.get("/tickets/ticket-does-not-exist")
    assert response.status_code == 404
    assert "ticket-demo-001" not in response.text
    assert "陈晓明" not in response.text
