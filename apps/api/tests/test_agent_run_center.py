"""T028 AI Execution Center 只读聚合接口测试。"""
import pytest
from fastapi.testclient import TestClient

from database import SessionLocal
from main import app
from seed_data import seed_demo_data

client = TestClient(app)


@pytest.fixture(autouse=True)
def deterministic_state():
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()
    yield


def test_agent_run_center_lists_real_runs_with_ticket_context():
    low = client.post("/tickets/ticket-demo-001/agent-runs")
    high = client.post("/tickets/ticket-demo-002/agent-runs")
    assert low.status_code == 201
    assert high.status_code == 201

    response = client.get("/agent-runs")
    assert response.status_code == 200
    items = response.json()
    low_key = low.json()["business_key"]
    high_key = high.json()["business_key"]
    items_by_key = {
        item["agent_run"]["business_key"]: item
        for item in items
    }

    # 产品接口返回全部真实 Run；共享测试库可能还包含其它测试创建的记录。
    assert {low_key, high_key}.issubset(items_by_key)
    ordered_keys = [item["agent_run"]["business_key"] for item in items]
    assert ordered_keys.index(high_key) < ordered_keys.index(low_key)

    low_item = items_by_key[low_key]
    assert low_item["ticket"]["business_key"] == "ticket-demo-001"
    assert low_item["agent_run"]["status"] == "completed"

    high_item = items_by_key[high_key]
    assert high_item["ticket"]["business_key"] == "ticket-demo-002"
    assert high_item["ticket"]["customer"]["name"]
    assert high_item["ticket"]["order"]["amount"] == "1299.00"
    assert high_item["agent_run"]["status"] == "waiting_for_approval"
    assert high_item["agent_run"]["approval_request"]["status"] == "pending"


def test_agent_run_detail_preserves_rejected_semantics_and_reason():
    started = client.post("/tickets/ticket-demo-002/agent-runs").json()
    run_key = started["business_key"]
    approval_key = started["approval_request"]["approval_key"]

    rejected = client.post(
        f"/approval-requests/{approval_key}/reject",
        json={"decision_reason": "超出本次人工授权范围"},
    )
    assert rejected.status_code == 200

    response = client.get(f"/agent-runs/{run_key}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["agent_run"]["status"] == "cancelled"
    assert detail["agent_run"]["approval_request"]["status"] == "rejected"
    assert (
        detail["agent_run"]["approval_request"]["decision_reason"]
        == "超出本次人工授权范围"
    )
    assert detail["agent_run"]["replacement"] is None
    assert detail["agent_run"]["ticket_result"]["resolution"] is None


def test_agent_run_detail_unknown_key_is_not_found():
    response = client.get("/agent-runs/run-does-not-exist")
    assert response.status_code == 404
