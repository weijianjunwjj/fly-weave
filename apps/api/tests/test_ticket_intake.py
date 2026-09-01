from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from database import SessionLocal, engine, get_db
from main import app
from seed_data import seed_demo_data


@pytest.fixture
def isolated_client():
    """在外层事务内验证真实 persistence，测试结束后不污染共享验收数据。"""

    connection = engine.connect()
    transaction = connection.begin()
    db = SessionLocal(bind=connection)

    def isolated_db():
        yield db

    app.dependency_overrides[get_db] = isolated_db
    try:
        seed_demo_data(db)
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
        transaction.rollback()
        connection.close()


def test_formal_ticket_intake_persists_and_enters_existing_approval_workflow(
    isolated_client: TestClient,
):
    """高金额受理数据经正式 API 进入既有 Run/Risk/Approval 链路且保持 pending。"""

    order_key = f"ORD-T027-{uuid4().hex[:12]}"
    create_response = isolated_client.post(
        "/tickets",
        json={
            "customer_name": "张先生",
            "customer_email": "zhang.t027@example.com",
            "issue_type": "商品损坏",
            "issue_description": "客户反馈商品到货后无法正常使用，希望申请换货。",
            "order_id": order_key,
            "order_amount": 899,
        },
    )
    assert create_response.status_code == 201
    created = create_response.json()
    ticket_key = created["business_key"]
    assert created["is_demo_data"] is False
    assert created["issue_type"] == "商品损坏"
    assert created["customer"]["name"] == "张先生"
    assert created["order"]["business_key"] == order_key
    assert created["order"]["amount"] == "899.00"
    assert created["updated_at"]

    persisted_response = isolated_client.get(f"/tickets/{ticket_key}")
    assert persisted_response.status_code == 200
    assert persisted_response.json()["order"]["amount"] == "899.00"

    run_response = isolated_client.post(f"/tickets/{ticket_key}/agent-runs")
    assert run_response.status_code == 201
    run = run_response.json()
    assert run["status"] == "waiting_for_approval"
    assert run["recommendation"]["action"] == "replacement"
    assert run["risk"]["requires_approval"] is True
    assert run["risk"]["order_amount"] == "899.00"
    assert run["approval_request"]["status"] == "pending"

    inbox_response = isolated_client.get("/approval-requests")
    assert inbox_response.status_code == 200
    matching = [
        item for item in inbox_response.json()
        if item["ticket"]["business_key"] == ticket_key
    ]
    assert len(matching) == 1
    assert matching[0]["approval"]["status"] == "pending"
