from fastapi.testclient import TestClient

from database import SessionLocal, get_db
from main import app
from seed_data import seed_demo_data


client = TestClient(app)


def test_list_tickets_returns_seeded_demo_tickets():
    """验证工单端点能返回 T005 播种的持久化演示工单"""
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()

    response = client.get("/tickets")
    assert response.status_code == 200

    tickets = response.json()
    business_keys = {ticket["business_key"] for ticket in tickets}
    assert {"ticket-demo-001", "ticket-demo-002", "ticket-demo-003"}.issubset(business_keys)

    for ticket in tickets:
        assert ticket["is_demo_data"] is True
        assert "subject" in ticket
        assert "status" in ticket


def test_list_tickets_reports_failure_when_database_is_unavailable():
    """验证数据库故障会返回显式失败，而不是伪造成功的工单数据"""
    def failing_get_db():
        raise RuntimeError("simulated database outage")
        yield None

    app.dependency_overrides[get_db] = failing_get_db
    failure_client = TestClient(app, raise_server_exceptions=False)
    try:
        response = failure_client.get("/tickets")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 500
    assert "ticket-demo-001" not in response.text
