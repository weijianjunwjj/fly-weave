"""T026 Approval Inbox 聚合读取与真实决策闭环测试。"""
import pytest
from fastapi.testclient import TestClient

from agent_run_service import run_golden_path
from approval_service import get_pending_approval
from database import SessionLocal
from main import app
from models import AgentRun, Ticket
from seed_data import seed_demo_data

client = TestClient(app)
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"


@pytest.fixture(autouse=True)
def deterministic_state():
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


def create_pending_approval() -> tuple[str, str]:
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(
            Ticket.business_key == HIGH_VALUE_TICKET_KEY
        ).one()
        run = run_golden_path(db, ticket)
        approval = get_pending_approval(db, run)
        assert approval is not None
        return approval.business_key, run.business_key
    finally:
        db.close()


def test_inbox_empty_then_pending_contains_product_context():
    assert client.get("/approval-requests").json() == []

    approval_key, run_key = create_pending_approval()
    response = client.get("/approval-requests")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    item = items[0]

    assert item["approval"]["approval_key"] == approval_key
    assert item["approval"]["status"] == "pending"
    assert item["approval"]["agent_run_key"] == run_key
    assert item["approval"]["risk"]["level"] == "high"
    assert item["approval"]["risk"]["requires_approval"] is True
    assert item["ticket"]["business_key"] == HIGH_VALUE_TICKET_KEY
    assert item["ticket"]["customer"]["name"]
    assert item["ticket"]["order"]["amount"] == "1299.00"
    assert item["agent_run"]["status"] == "waiting_for_approval"
    assert item["agent_run"]["policy_basis"]["status"] == "success"
    assert item["agent_run"]["policy_basis"]["passages"]


def test_approve_resume_is_visible_as_completed_with_audit():
    approval_key, run_key = create_pending_approval()

    approved = client.post(
        f"/approval-requests/{approval_key}/approve",
        json={"decision_reason": "人工核验通过"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    resumed = client.post(f"/agent-runs/{run_key}/resume")
    assert resumed.status_code == 200
    assert resumed.json()["agent_run"]["status"] == "completed"
    assert resumed.json()["agent_run"]["ticket_result"]["status"] == "resolved"

    item = client.get("/approval-requests").json()[0]
    assert item["approval"]["status"] == "approved"
    assert item["approval"]["decision_reason"] == "人工核验通过"
    assert item["agent_run"]["status"] == "completed"
    assert item["agent_run"]["ticket_result"]["resolved_at"] is not None

    events = client.get(f"/agent-runs/{run_key}/audit-events").json()
    event_types = [event["event_type"] for event in events]
    assert "approval_approved" in event_types
    assert "create_replacement" in event_types
    assert "update_ticket" in event_types
    assert event_types[-1] == "agent_run_outcome"


def test_reject_is_visible_and_protected_action_does_not_execute():
    approval_key, run_key = create_pending_approval()

    rejected = client.post(
        f"/approval-requests/{approval_key}/reject",
        json={"decision_reason": "业务影响过高"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["agent_run_status"] == "cancelled"

    item = client.get("/approval-requests").json()[0]
    assert item["approval"]["status"] == "rejected"
    assert item["approval"]["decision_reason"] == "业务影响过高"
    assert item["agent_run"]["status"] == "cancelled"
    assert item["agent_run"]["replacement"] is None
    assert item["agent_run"]["ticket_result"]["status"] != "resolved"

    events = client.get(f"/agent-runs/{run_key}/audit-events").json()
    assert "approval_rejected" in [event["event_type"] for event in events]
    assert not any(
        event["event_type"] == "create_replacement" and event["success"]
        for event in events
    )
