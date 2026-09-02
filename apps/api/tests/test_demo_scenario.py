"""T030 Demo Scenario bootstrap 回归测试。

约束：demo bootstrap 只能创建真实业务输入（Customer / Order / Ticket），后续的
AgentRun、Policy、Risk、Approval 与 Audit 一律复用正式执行链路，绝不伪造。
"""
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


def test_high_risk_demo_bootstrap_enters_real_approval_workflow(
    isolated_client: TestClient,
):
    """demo bootstrap 只准备业务输入，随后走正式 Run/Risk/Approval 链路并停在 pending。"""

    created = isolated_client.post("/demo/scenarios/high-risk-replacement")
    assert created.status_code == 201
    ticket = created.json()
    ticket_key = ticket["business_key"]

    # 仅创建真实业务输入：工单、订单、客户均为 demo 标记，金额满足高风险门槛。
    assert ticket["is_demo_data"] is True
    assert ticket["demo_scenario"] == "high_risk_replacement"
    assert ticket["issue_type"] == "换货"
    assert ticket["customer"]["name"] == "演示客户"
    assert ticket["order"]["amount"] == "1299.00"
    assert ticket["order"]["business_key"].startswith("demo-order-")

    # 进入既有正式 Agent Run 链路，而不是演示专属执行逻辑。
    run_response = isolated_client.post(f"/tickets/{ticket_key}/agent-runs")
    assert run_response.status_code == 201
    run = run_response.json()
    assert run["status"] == "waiting_for_approval"
    assert run["recommendation"]["action"] == "replacement"
    assert run["risk"]["level"] == "high"
    assert run["risk"]["requires_approval"] is True
    assert run["approval_request"]["status"] == "pending"

    # 同一真实业务对象在 Approval Inbox 中可见。
    inbox = isolated_client.get("/approval-requests")
    assert inbox.status_code == 200
    matching = [
        item for item in inbox.json()
        if item["ticket"]["business_key"] == ticket_key
    ]
    assert len(matching) == 1
    assert matching[0]["approval"]["status"] == "pending"


def test_high_risk_demo_bootstrap_is_repeatable_with_unique_ids(
    isolated_client: TestClient,
):
    """每次运行 demo 都生成全新的唯一业务标识，绝不复用历史 demo 对象。"""

    first = isolated_client.post("/demo/scenarios/high-risk-replacement").json()
    second = isolated_client.post("/demo/scenarios/high-risk-replacement").json()

    assert first["business_key"] != second["business_key"]
    assert first["order"]["business_key"] != second["order"]["business_key"]


def test_demo_bootstrap_is_unavailable_outside_demo_environments(
    monkeypatch, isolated_client: TestClient,
):
    """demo bootstrap 在非 demo 环境一律不可用，不暴露为正式客服 API。"""

    from main import settings

    original = settings.app_env
    monkeypatch.setattr(settings, "app_env", "production")
    try:
        response = isolated_client.post("/demo/scenarios/high-risk-replacement")
        assert response.status_code == 404
    finally:
        monkeypatch.setattr(settings, "app_env", original)
