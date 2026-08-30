"""T023 Audit Trail 的确定性测试。

覆盖任务要求的验收点：

- 低风险 Golden Path 完整审计链（decision / get_order / check_inventory /
  risk allow / create_replacement / update_ticket / completed）；
- 高风险 pending 审计（risk approval_required + approval_request_created，且不出现
  create_replacement success）；
- HUMAN approve / reject 审计来自真实 ApprovalRequest 状态迁移，不虚构 actor；
- approved Resume 增量审计（create_replacement → update_ticket → completed）；
- Tool 失败审计如实记录 failure，不伪造成功；
- 同决策重试 / 并发 approve / 并发 Resume 只产生一条语义事件；
- restart / 全新 DB Session 后仍可查询；
- unknown AgentRun 返回 404；
- secret 与客户敏感信息不进入审计记录；
- 审计顺序 deterministic；
- T019–T022 回归（reject 终止 Run、低风险无审批、高风险仍 pending）。

全部前置数据来自 ``seed_demo_data`` 的种子工单，流程全程走 T011~T022 的真实实现，
测试不手工插入 AuditEvent，也不伪造任何事件状态。
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from agent_run_service import resume_agent_run, run_golden_path
from approval_decision_service import approve, reject
from approval_service import get_pending_approval
from database import SessionLocal
from main import app
from models import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    AuditEvent,
    InventoryItem,
    Ticket,
)
from seed_data import seed_demo_data

client = TestClient(app)

# 低风险场景：金额 299、有货、窗口内 —— 唯一能走完整条成功路径的种子工单
LOW_RISK_TICKET_KEY = "ticket-demo-001"
# 高金额场景：业务条件 eligible，但金额 1299 超过政策阈值 500
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"

AVAILABLE_SKU = "SKU-EARBUD-PRO-01"

# 低风险 Golden Path 的真实审计事件顺序（与 Golden Path 执行顺序一致）
LOW_RISK_EVENT_ORDER = [
    "get_order",
    "check_inventory",
    "decision_produced",
    "risk_gate",
    "create_replacement",
    "update_ticket",
    "agent_run_outcome",
]

# 高风险 pending 的审计事件顺序（被风险门禁拦下，换货未发生）
HIGH_RISK_PENDING_EVENT_ORDER = [
    "get_order",
    "check_inventory",
    "decision_produced",
    "risk_gate",
    "approval_request_created",
]


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据。

    ``seed_demo_data`` 会先清空 demo 工单，数据库级 ON DELETE CASCADE 随之清除
    AgentRun、审批请求与审计事件，因此测试可反复运行且互不干扰。
    """
    _seed()
    yield
    _seed()


def _seed() -> None:
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


def _run_golden_path(ticket_key: str) -> str:
    """对给定种子工单执行一次真实完整流程，返回本次 Run 的业务标识。"""
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.business_key == ticket_key).one()
        return run_golden_path(db, ticket).business_key
    finally:
        db.close()


def _pending_approval_key(run_key: str) -> str:
    """取回该 Run 唯一 pending 审批请求的业务标识。"""
    db = SessionLocal()
    try:
        run = db.query(AgentRun).filter(AgentRun.business_key == run_key).one()
        approval = get_pending_approval(db, run)
        assert approval is not None
        return approval.business_key
    finally:
        db.close()


def _approve(approval_key: str) -> None:
    db = SessionLocal()
    try:
        approve(db, approval_key, "金额核实无误，同意换货")
    finally:
        db.close()


def _audit_events_via_api(run_key: str) -> list[dict]:
    """通过查询 API 取回一次 Run 的审计时间线（同时验证端点返回）。"""
    response = client.get(f"/agent-runs/{run_key}/audit-events")
    assert response.status_code == 200
    return response.json()


def _event_types(events: list[dict]) -> list[str]:
    return [event["event_type"] for event in events]


def _by_type(events: list[dict]) -> dict[str, dict]:
    return {event["event_type"]: event for event in events}


def _audit_rows(run_key: str) -> list[AuditEvent]:
    """在全新 session 中读回审计事件，证明其真实持久化。"""
    db = SessionLocal()
    try:
        run = db.query(AgentRun).filter(AgentRun.business_key == run_key).one()
        return (
            db.query(AuditEvent)
            .filter(AuditEvent.agent_run_id == run.id)
            .order_by(AuditEvent.id.asc())
            .all()
        )
    finally:
        db.close()


# --------------------------------------------------------------------------
# 低风险 Golden Path
# --------------------------------------------------------------------------


def test_low_risk_golden_path_audit_timeline():
    """低风险 Golden Path 完成后的审计链完整、顺序确定、成功标记全部为真。"""
    run_key = _run_golden_path(LOW_RISK_TICKET_KEY)
    events = _audit_events_via_api(run_key)

    assert _event_types(events) == LOW_RISK_EVENT_ORDER

    by_type = _by_type(events)
    assert by_type["decision_produced"]["outcome"] == "eligible"
    assert by_type["decision_produced"]["success"] is True
    assert by_type["get_order"]["outcome"] == "success"
    assert by_type["get_order"]["success"] is True
    assert by_type["check_inventory"]["outcome"] == "success"
    assert by_type["check_inventory"]["success"] is True
    assert by_type["risk_gate"]["outcome"] == "allow"
    assert by_type["risk_gate"]["success"] is True
    assert by_type["create_replacement"]["outcome"] == "created"
    assert by_type["create_replacement"]["success"] is True
    assert by_type["update_ticket"]["outcome"] == "updated"
    assert by_type["update_ticket"]["success"] is True
    assert by_type["agent_run_outcome"]["outcome"] == "completed"
    assert by_type["agent_run_outcome"]["success"] is True


def test_low_risk_audit_references_affected_objects():
    """审计事件引用 AgentRun 与相关业务对象，且业务对象标识真实可追溯。"""
    run_key = _run_golden_path(LOW_RISK_TICKET_KEY)
    events = _audit_events_via_api(run_key)
    by_type = _by_type(events)

    assert by_type["get_order"]["affected_object_type"] == "order"
    assert by_type["get_order"]["affected_object_key"] == "order-demo-001"
    assert by_type["check_inventory"]["affected_object_type"] == "inventory_item"
    assert by_type["check_inventory"]["affected_object_key"] == AVAILABLE_SKU
    assert by_type["create_replacement"]["affected_object_type"] == "replacement_order"
    assert by_type["create_replacement"]["affected_object_key"] == "replacement-order-demo-001"
    assert by_type["update_ticket"]["affected_object_type"] == "ticket"
    assert by_type["update_ticket"]["affected_object_key"] == LOW_RISK_TICKET_KEY
    assert by_type["agent_run_outcome"]["affected_object_type"] == "agent_run"
    assert by_type["agent_run_outcome"]["affected_object_key"] == run_key


# --------------------------------------------------------------------------
# 高风险 pending
# --------------------------------------------------------------------------


def test_high_risk_pending_audit_has_approval_required_and_no_replacement():
    """高风险被风险门禁拦下：approval_required + 审批请求，且不出现换货成功。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    events = _audit_events_via_api(run_key)

    assert _event_types(events) == HIGH_RISK_PENDING_EVENT_ORDER

    by_type = _by_type(events)
    assert by_type["risk_gate"]["outcome"] == "approval_required"
    assert by_type["risk_gate"]["success"] is False
    assert by_type["approval_request_created"]["outcome"] == "created"
    assert by_type["approval_request_created"]["success"] is True
    assert by_type["approval_request_created"]["actor_type"] == "system"

    # 受保护动作没有发生：不出现 create_replacement / update_ticket / completed
    assert "create_replacement" not in by_type
    assert "update_ticket" not in by_type
    assert "agent_run_outcome" not in by_type


# --------------------------------------------------------------------------
# HUMAN approve / reject
# --------------------------------------------------------------------------


def test_approve_records_human_approval_audit():
    """approve 只记录 HUMAN 批准事件，不恢复 Run、不产生后续执行事件。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        approve(db, approval_key, "金额核实无误，同意换货")
    finally:
        db.close()

    events = _audit_events_via_api(run_key)
    by_type = _by_type(events)
    assert "approval_approved" in by_type
    approved = by_type["approval_approved"]

    assert approved["actor_type"] == "human"
    assert approved["outcome"] == "approved"
    assert approved["success"] is True
    assert approved["reference_type"] == "approval"
    assert approved["reference_key"] == approval_key
    assert approved["affected_object_type"] == "approval_request"
    assert approved["affected_object_key"] == approval_key
    # 批准不推进业务：不出现换货 / 回写 / 完成终态
    assert "create_replacement" not in by_type
    assert "agent_run_outcome" not in by_type


def test_reject_records_human_rejection_audit_and_no_replacement_success():
    """reject 记录 HUMAN 拒绝事件，且不出现 create_replacement success。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        reject(db, approval_key, "超过授权金额上限，拒绝换货")
    finally:
        db.close()

    events = _audit_events_via_api(run_key)
    by_type = _by_type(events)
    assert "approval_rejected" in by_type
    rejected = by_type["approval_rejected"]

    assert rejected["actor_type"] == "human"
    assert rejected["outcome"] == "rejected"
    assert rejected["success"] is False
    assert rejected["reference_key"] == approval_key

    # 拒绝之后绝不出现受保护动作成功
    assert not any(
        event["event_type"] == "create_replacement" and event["success"]
        for event in events
    )
    assert "create_replacement" not in by_type
    assert "update_ticket" not in by_type
    assert "agent_run_outcome" not in by_type


# --------------------------------------------------------------------------
# approved Resume
# --------------------------------------------------------------------------


def test_approved_resume_produces_incremental_execution_audit():
    """approved Resume 补上 create_replacement → update_ticket → completed 增量审计。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    db = SessionLocal()
    try:
        resumed = resume_agent_run(db, run_key)
        assert resumed.status is AgentRunStatus.COMPLETED
    finally:
        db.close()

    events = _audit_events_via_api(run_key)
    assert _event_types(events) == [
        "get_order",
        "check_inventory",
        "decision_produced",
        "risk_gate",
        "approval_request_created",
        "approval_approved",
        "create_replacement",
        "update_ticket",
        "agent_run_outcome",
    ]

    by_type = _by_type(events)
    # 风险门禁仍保留首次进入审批时的 approval_required，不被 resume 重复覆盖
    assert by_type["risk_gate"]["outcome"] == "approval_required"
    assert by_type["create_replacement"]["outcome"] == "created"
    assert by_type["create_replacement"]["success"] is True
    assert by_type["update_ticket"]["outcome"] == "updated"
    assert by_type["agent_run_outcome"]["outcome"] == "completed"
    assert by_type["agent_run_outcome"]["success"] is True


# --------------------------------------------------------------------------
# Tool 失败不伪造成功
# --------------------------------------------------------------------------


def test_tool_failure_audit_records_failure_not_success():
    """库存 Tool 真实失败（查无 SKU）时审计如实记录 failure，后续事件不出现。"""
    db = SessionLocal()
    try:
        db.query(InventoryItem).filter(
            InventoryItem.product_sku == AVAILABLE_SKU
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

    run_key = _run_golden_path(LOW_RISK_TICKET_KEY)
    events = _audit_events_via_api(run_key)

    assert _event_types(events) == [
        "get_order",
        "check_inventory",
        "agent_run_outcome",
    ]

    by_type = _by_type(events)
    assert by_type["check_inventory"]["outcome"] == "sku_not_found"
    assert by_type["check_inventory"]["success"] is False
    assert by_type["agent_run_outcome"]["outcome"] == "failed"
    assert by_type["agent_run_outcome"]["success"] is False
    # 失败之后没有 decision / create_replacement / update_ticket
    assert "decision_produced" not in by_type
    assert "create_replacement" not in by_type
    assert "update_ticket" not in by_type


# --------------------------------------------------------------------------
# 幂等 / 并发去重
# --------------------------------------------------------------------------


def test_same_decision_retry_produces_single_audit_event():
    """同决策 API 重试不产生第二条 approve 审计事件。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        approve(db, approval_key, "首次批准的理由")
        approve(db, approval_key, "重试不应产生第二条事件")
    finally:
        db.close()

    events = _audit_events_via_api(run_key)
    approved = [event for event in events if event["event_type"] == "approval_approved"]
    assert len(approved) == 1


def test_concurrent_approval_produces_single_audit_event():
    """并发 approve 只有赢得状态迁移的一方产生一条审批审计事件。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    barrier = threading.Barrier(2)

    def do_approve() -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                approve(db, approval_key, "并发批准")
            except Exception:  # noqa: BLE001 - 幂等重试不抛，冲突也已被测试覆盖
                pass
        finally:
            db.close()

    first = threading.Thread(target=do_approve)
    second = threading.Thread(target=do_approve)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    events = _audit_events_via_api(run_key)
    approved = [event for event in events if event["event_type"] == "approval_approved"]
    assert len(approved) == 1


def test_concurrent_resume_produces_single_execution_audit():
    """并发 Resume 只产生一条 create_replacement 与一条 agent_run_outcome。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    barrier = threading.Barrier(2)

    def do_resume() -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                resume_agent_run(db, run_key)
            except Exception:  # noqa: BLE001 - 冲突 / 幂等返回均不算错误
                pass
        finally:
            db.close()

    first = threading.Thread(target=do_resume)
    second = threading.Thread(target=do_resume)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    events = _audit_events_via_api(run_key)
    created = [event for event in events if event["event_type"] == "create_replacement"]
    outcomes = [event for event in events if event["event_type"] == "agent_run_outcome"]
    assert len(created) == 1
    assert len(outcomes) == 1


# --------------------------------------------------------------------------
# 持久化与确定性排序
# --------------------------------------------------------------------------


def test_audit_events_queryable_in_brand_new_session():
    """重启 / 全新 DB Session 后仍能读回完整审计事件。"""
    run_key = _run_golden_path(LOW_RISK_TICKET_KEY)
    rows = _audit_rows(run_key)

    assert [row.event_type.value for row in rows] == LOW_RISK_EVENT_ORDER
    assert len(rows) == 7


def test_audit_order_is_deterministic():
    """同一 Run 两次查询返回完全相同、按时间升序的事件顺序。"""
    run_key = _run_golden_path(LOW_RISK_TICKET_KEY)

    first = _audit_events_via_api(run_key)
    second = _audit_events_via_api(run_key)

    assert [event["event_key"] for event in first] == [
        event["event_key"] for event in second
    ]
    occurred = [event["occurred_at"] for event in first]
    assert occurred == sorted(occurred)


def test_unknown_agent_run_audit_returns_404():
    """未知 AgentRun 返回 404，绝不返回空列表冒充"没有事件"。"""
    response = client.get("/agent-runs/run-does-not-exist/audit-events")
    assert response.status_code == 404


# --------------------------------------------------------------------------
# secret / 敏感信息
# --------------------------------------------------------------------------


def test_audit_events_do_not_contain_sensitive_data():
    """审计记录不写入客户敏感信息、凭据或常见 secret 字样。"""
    run_key = _run_golden_path(LOW_RISK_TICKET_KEY)
    events = _audit_events_via_api(run_key)
    serialized = json.dumps(events, ensure_ascii=False)

    # 种子里的客户 email / 电话不得进入审计
    assert "@example.com" not in serialized
    assert "138-" not in serialized
    # 数据库连接串（含凭据）不得进入审计
    assert "postgresql://" not in serialized
    # 常见 secret 关键词不得进入审计
    lower = serialized.lower()
    assert "password" not in lower
    assert "api_key" not in lower
    assert "secret" not in lower


# --------------------------------------------------------------------------
# T019–T022 回归
# --------------------------------------------------------------------------


def test_t019_t022_regressions_with_audit():
    """高风险仍 pending、reject 终止 Run、低风险直接完成，且审计与状态一致。"""
    # 高风险 pending
    high_run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    high_events = _audit_events_via_api(high_run_key)
    assert _event_types(high_events) == HIGH_RISK_PENDING_EVENT_ORDER

    # reject 终止 Run
    approval_key = _pending_approval_key(high_run_key)
    db = SessionLocal()
    try:
        reject(db, approval_key, "拒绝")
    finally:
        db.close()
    db = SessionLocal()
    try:
        run = db.query(AgentRun).filter(AgentRun.business_key == high_run_key).one()
        assert run.status is AgentRunStatus.CANCELLED
    finally:
        db.close()
    after_reject = _audit_events_via_api(high_run_key)
    assert not any(
        event["event_type"] == "create_replacement" and event["success"]
        for event in after_reject
    )

    # 低风险直接完成，无审批请求
    low_run_key = _run_golden_path(LOW_RISK_TICKET_KEY)
    db = SessionLocal()
    try:
        run = db.query(AgentRun).filter(AgentRun.business_key == low_run_key).one()
        assert run.status is AgentRunStatus.COMPLETED
        assert get_pending_approval(db, run) is None
    finally:
        db.close()
    low_events = _audit_events_via_api(low_run_key)
    assert "approval_request_created" not in _event_types(low_events)
    assert _event_types(low_events) == LOW_RISK_EVENT_ORDER


def test_approval_request_count_is_single_after_audit_trail():
    """审计接入不破坏 ApprovalRequest 的唯一性（T020 回归）。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    db = SessionLocal()
    try:
        run = db.query(AgentRun).filter(AgentRun.business_key == run_key).one()
        assert db.query(ApprovalRequest).filter(
            ApprovalRequest.agent_run_id == run.id
        ).count() == 1
    finally:
        db.close()
