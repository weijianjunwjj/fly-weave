"""T021 人工审批决策闭环的确定性测试。

覆盖任务要求的验收点：

- approve：只把审批请求记录为 APPROVED，不执行换货、不回写工单、不恢复 Run，
  也不把 Run 标 COMPLETED；
- reject：把审批请求记录为 REJECTED，并让仍停在等待审批的 Run 转入既有
  CANCELLED 终止态，不产生换货单、不更新工单；
- 同决策重试幂等：不刷新 resolved_at、不覆盖首次 decision_reason、不改动 risk
  snapshot，也不重复产生任何业务副作用；
- 相反决策返回 409：APPROVED 后 reject、REJECTED 后 approve 都不可翻转；
- approve/reject 并发时只有一次 transition 成功，最终数据库状态只能是
  APPROVED 或 REJECTED 之一，绝不可能是 pending 或双 transition；
- 决策结果真持久化：关闭 session 后在全新 session 中仍能读回；
- risk snapshot 在决策前后逐项保持不变（不重算历史风险）；
- safe path 不产生任何审批请求；
- T019/T020/Golden Path 语义不回归（低风险完成、高风险暂停产生 pending 请求）。

全部前置数据来自 ``seed_demo_data`` 的种子工单，高风险路径全程走 T011~T019 的
真实实现，测试不手工插入审批请求，也不伪造任何风险判断。
"""
from decimal import Decimal
import threading

import pytest
from fastapi.testclient import TestClient

from agent_run_service import run_golden_path
from approval_decision_service import (
    ApprovalConflictError,
    ApprovalRequestNotFoundError,
    approve,
    reject,
)
from approval_service import get_pending_approval, risk_snapshot_of
from approvals import ApprovalRequestStatus
from database import SessionLocal
from main import app
from models import (
    AgentRun,
    AgentRunStatus,
    ApprovalRequest,
    Order,
    ReplacementOrder,
    Ticket,
    TicketStatus,
)
from risk import ProtectedAction, RiskLevel, RiskRuleCode
from seed_data import seed_demo_data

client = TestClient(app)

# 低风险场景：金额 299、有货、窗口内 —— 唯一能走完整条成功路径的种子工单
LOW_RISK_TICKET_KEY = "ticket-demo-001"
LOW_RISK_ORDER_KEY = "order-demo-001"
# 高金额场景：业务条件 eligible，但金额 1299 超过政策阈值 500
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"
HIGH_VALUE_ORDER_KEY = "order-demo-002"

POLICY_KEY = "policy-replacement-standard"
SEEDED_ORDER_AMOUNT = Decimal("1299.00")
SEEDED_THRESHOLD = Decimal("500.00")


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据。

    ``seed_demo_data`` 会先清空 demo 工单，数据库级 ON DELETE CASCADE 随之清除
    本模块产生的 AgentRun、步骤、换货单与审批请求，因此测试可反复运行且互不干扰。
    """
    _reset()
    yield
    _reset()


def _reset() -> None:
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


def _run_golden_path(ticket_key: str) -> str:
    """对给定种子工单执行一次真实的完整流程，返回本次 Run 的业务标识。"""
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


def _approval(db, approval_key: str) -> ApprovalRequest:
    return (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.business_key == approval_key)
        .one()
    )


def _run(db, run_key: str) -> AgentRun:
    return db.query(AgentRun).filter(AgentRun.business_key == run_key).one()


def _start_high_risk_via_api() -> str:
    """通过 POST 端点启动一次高风险 Run，返回其 pending 审批请求标识。"""
    response = client.post(f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs")
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "waiting_for_approval"
    assert payload["approval_request"] is not None
    return payload["approval_request"]["approval_key"]


# --------------------------------------------------------------------------
# approve：只记录 APPROVED，不推进任何业务
# --------------------------------------------------------------------------


def test_approve_records_approved_without_any_business_side_effect():
    """approve 只持久化 APPROVED；不换货、不回写工单、不恢复 Run、不标 COMPLETED。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        decided = approve(db, approval_key, "人工确认金额合理，同意换货")
        assert decided.status is ApprovalRequestStatus.APPROVED
        assert decided.resolved_at is not None
        assert decided.decision_reason == "人工确认金额合理，同意换货"
    finally:
        db.close()

    db = SessionLocal()
    try:
        approval = _approval(db, approval_key)
        assert approval.status is ApprovalRequestStatus.APPROVED
        assert approval.resolved_at is not None
        assert approval.decision_reason == "人工确认金额合理，同意换货"

        # 受保护动作没有被执行：不产生换货单，工单也不回写。
        order = (
            db.query(Order).filter(Order.business_key == HIGH_VALUE_ORDER_KEY).one()
        )
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.order_id == order.id)
            .one_or_none()
            is None
        )
        run = _run(db, run_key)
        assert run.replacement is None

        ticket = run.ticket
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
        assert ticket.resolved_at is None
        assert ticket.replacement_id is None

        # approve 不恢复 Run：仍停在等待审批，绝不标 COMPLETED。
        assert run.status is AgentRunStatus.WAITING_FOR_APPROVAL
        assert run.completed_at is None
        assert run.error_message is None
    finally:
        db.close()


# --------------------------------------------------------------------------
# reject：记录 REJECTED，并把 Run 转入 CANCELLED 终止态
# --------------------------------------------------------------------------


def test_reject_records_rejected_and_cancels_the_run():
    """reject 持久化 REJECTED，并让等待审批的 Run 转入既有 CANCELLED 终止态。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        decided = reject(db, approval_key, "超过授权金额上限，拒绝换货")
        assert decided.status is ApprovalRequestStatus.REJECTED
        assert decided.resolved_at is not None
        assert decided.decision_reason == "超过授权金额上限，拒绝换货"
    finally:
        db.close()

    db = SessionLocal()
    try:
        approval = _approval(db, approval_key)
        assert approval.status is ApprovalRequestStatus.REJECTED
        assert approval.resolved_at is not None

        run = _run(db, run_key)
        # Run 不再保持 WAITING_FOR_APPROVAL，进入现有最合适的终止态 CANCELLED。
        assert run.status is AgentRunStatus.CANCELLED
        assert run.status is not AgentRunStatus.WAITING_FOR_APPROVAL
        # 终止不是成功完成，但 Run 确实已经结束。
        assert run.completed_at is not None
        # 拒绝不是执行失败，不伪造 error_message。
        assert run.error_message is None

        # 不产生换货单，工单也不更新。
        order = (
            db.query(Order).filter(Order.business_key == HIGH_VALUE_ORDER_KEY).one()
        )
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.order_id == order.id)
            .one_or_none()
            is None
        )
        ticket = run.ticket
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
        assert ticket.replacement_id is None
    finally:
        db.close()


# --------------------------------------------------------------------------
# 幂等与冲突
# --------------------------------------------------------------------------


def test_same_decision_retry_is_idempotent():
    """同决策重试返回当前结果，不刷新 resolved_at，也不覆盖首次 decision_reason。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        first = approve(db, approval_key, "首次批准的理由")
        first_resolved_at = first.resolved_at
    finally:
        db.close()

    db = SessionLocal()
    try:
        # 用不同的理由重试：幂等返回应保留第一次的理由，而不是被覆盖。
        second = approve(db, approval_key, "这条理由不应生效")
        assert second.status is ApprovalRequestStatus.APPROVED
        assert second.resolved_at == first_resolved_at
        assert second.decision_reason == "首次批准的理由"
    finally:
        db.close()

    # 数据库里也只有第一次的审计事实，没有被刷新。
    db = SessionLocal()
    try:
        approval = _approval(db, approval_key)
        assert approval.status is ApprovalRequestStatus.APPROVED
        assert approval.resolved_at == first_resolved_at
        assert approval.decision_reason == "首次批准的理由"
    finally:
        db.close()


def test_conflicting_decision_returns_conflict():
    """APPROVED 后 reject、REJECTED 后 approve 都不可翻转，抛出冲突。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        approve(db, approval_key, None)
        with pytest.raises(ApprovalConflictError):
            reject(db, approval_key, None)
        # 状态保持 APPROVED，没有被冲突请求改写。
        assert _approval(db, approval_key).status is ApprovalRequestStatus.APPROVED
    finally:
        db.close()


def test_reject_then_approve_returns_conflict():
    """REJECTED 后 approve 同样返回冲突。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        reject(db, approval_key, None)
        with pytest.raises(ApprovalConflictError):
            approve(db, approval_key, None)
        assert _approval(db, approval_key).status is ApprovalRequestStatus.REJECTED
    finally:
        db.close()


def test_unknown_approval_request_raises_not_found():
    """不存在的审批请求抛出 NotFound，而不是悄悄成功或冲突。"""
    db = SessionLocal()
    try:
        with pytest.raises(ApprovalRequestNotFoundError):
            approve(db, "approval-does-not-exist", None)
        with pytest.raises(ApprovalRequestNotFoundError):
            reject(db, "approval-does-not-exist", None)
    finally:
        db.close()


# --------------------------------------------------------------------------
# 并发：approve 与 reject 只能有一个 transition 成功
# --------------------------------------------------------------------------


def test_approve_and_reject_concurrently_only_one_transition_succeeds():
    """并发 approve/reject 时，最终只有 APPROVED 或 REJECTED 之一，绝不双 transition。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    outcomes: list[tuple[str, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def decide(target: ApprovalRequestStatus) -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                if target is ApprovalRequestStatus.APPROVED:
                    approve(db, approval_key, "并发批准")
                    verdict = "approved"
                else:
                    reject(db, approval_key, "并发拒绝")
                    verdict = "rejected"
                with lock:
                    outcomes.append(("ok", verdict))
            except ApprovalConflictError:
                with lock:
                    outcomes.append(("conflict", target.value))
            except Exception as exc:  # noqa: BLE001 - 让意外失败显式暴露
                with lock:
                    outcomes.append(("error", repr(exc)))
        finally:
            db.close()

    approve_thread = threading.Thread(
        target=decide, args=(ApprovalRequestStatus.APPROVED,)
    )
    reject_thread = threading.Thread(
        target=decide, args=(ApprovalRequestStatus.REJECTED,)
    )
    approve_thread.start()
    reject_thread.start()
    approve_thread.join(timeout=30)
    reject_thread.join(timeout=30)

    assert not approve_thread.is_alive()
    assert not reject_thread.is_alive()

    # 恰好一次成功 + 一次冲突，没有 error。
    assert len(outcomes) == 2, outcomes
    oks = [o for o in outcomes if o[0] == "ok"]
    conflicts = [o for o in outcomes if o[0] == "conflict"]
    assert len(oks) == 1
    assert len(conflicts) == 1

    # 最终数据库状态只有赢家一个，且不可能是 pending。
    winner = oks[0][1]
    expected = (
        ApprovalRequestStatus.APPROVED
        if winner == "approved"
        else ApprovalRequestStatus.REJECTED
    )

    db = SessionLocal()
    try:
        approval = _approval(db, approval_key)
        assert approval.status is expected
        assert approval.status is not ApprovalRequestStatus.PENDING
        assert approval.resolved_at is not None
        # 只有一个决策理由被持久化（赢家的那个），不可能是另一个决策的。
        if expected is ApprovalRequestStatus.APPROVED:
            assert approval.decision_reason == "并发批准"
        else:
            assert approval.decision_reason == "并发拒绝"

        run = _run(db, run_key)
        if expected is ApprovalRequestStatus.REJECTED:
            assert run.status is AgentRunStatus.CANCELLED
        else:
            # approve 不恢复 Run，仍停在等待审批。
            assert run.status is AgentRunStatus.WAITING_FOR_APPROVAL
    finally:
        db.close()


# --------------------------------------------------------------------------
# 持久化与 snapshot 不变
# --------------------------------------------------------------------------


def test_decision_persists_across_a_brand_new_session():
    """关闭 session 后在全新 session 中仍能读回决策结果与理由。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    first = SessionLocal()
    try:
        approve(first, approval_key, "跨会话审计理由")
    finally:
        first.close()

    second = SessionLocal()
    try:
        approval = _approval(second, approval_key)
        assert approval.status is ApprovalRequestStatus.APPROVED
        assert approval.resolved_at is not None
        assert approval.decision_reason == "跨会话审计理由"
        # Run 关联在全新 session 中依然成立。
        assert approval.agent_run.business_key == run_key
        assert approval.agent_run.status is AgentRunStatus.WAITING_FOR_APPROVAL
    finally:
        second.close()


def test_risk_snapshot_is_unchanged_after_decision():
    """决策前后 persisted risk snapshot 逐项保持不变，不重算历史风险。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    before_db = SessionLocal()
    try:
        before = _approval(before_db, approval_key)
        before_snapshot = risk_snapshot_of(before)
    finally:
        before_db.close()

    db = SessionLocal()
    try:
        approve(db, approval_key, "同意")
    finally:
        db.close()

    after_db = SessionLocal()
    try:
        after = _approval(after_db, approval_key)
        after_snapshot = risk_snapshot_of(after)

        # typed 快照在决策前后完全相等。
        assert after_snapshot == before_snapshot

        # 落库列也逐项保持不变。
        assert after.protected_action is ProtectedAction.CREATE_REPLACEMENT
        assert after.risk_level is RiskLevel.HIGH
        assert (
            after.risk_rule_code
            is RiskRuleCode.ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD
        )
        assert after.risk_requires_approval is True
        assert after.reason == before.reason
        assert after.risk_order_key == HIGH_VALUE_ORDER_KEY
        assert after.risk_order_amount == SEEDED_ORDER_AMOUNT
        assert after.risk_approval_threshold_amount == SEEDED_THRESHOLD
        assert after.risk_policy_key == POLICY_KEY
    finally:
        after_db.close()


# --------------------------------------------------------------------------
# safe path 不产生审批请求（T019/T020/Golden Path regression）
# --------------------------------------------------------------------------


def test_safe_path_completes_without_any_approval_request():
    """低风险 Golden Path 正常完成，不产生任何审批请求。"""
    run_key = _run_golden_path(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.COMPLETED
        assert run.replacement is not None
        assert get_pending_approval(db, run) is None
        assert db.query(ApprovalRequest).count() == 0
    finally:
        db.close()


def test_high_risk_still_persists_pending_request_before_decision():
    """T019/T020 语义不回归：高风险暂停并产生 pending 请求，等待 T021 决策。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.WAITING_FOR_APPROVAL

        approval = get_pending_approval(db, run)
        assert approval is not None
        assert approval.status is ApprovalRequestStatus.PENDING
        assert approval.resolved_at is None
        assert approval.decision_reason is None
        assert approval.risk_order_amount == SEEDED_ORDER_AMOUNT
    finally:
        db.close()


# --------------------------------------------------------------------------
# API 端点：200 / 404 / 409 / 幂等 / 响应字段
# --------------------------------------------------------------------------


EXPECTED_DECISION_FIELDS = {
    "approval_key",
    "status",
    "protected_action",
    "agent_run_key",
    "agent_run_status",
    "resolved_at",
    "decision_reason",
    "risk",
}


def test_approve_endpoint_returns_persisted_decision_and_snapshot():
    """approve 端点返回真实持久化状态与完整 risk snapshot。"""
    approval_key = _start_high_risk_via_api()

    response = client.post(
        f"/approval-requests/{approval_key}/approve",
        json={"decision_reason": "金额核实无误，同意"},
    )
    assert response.status_code == 200
    payload = response.json()

    # 只暴露公开契约字段，不泄露自增主键或 ORM 内部字段。
    assert set(payload) == EXPECTED_DECISION_FIELDS
    assert "id" not in payload
    assert "agent_run_id" not in payload

    assert payload["approval_key"] == approval_key
    assert payload["status"] == "approved"
    assert payload["protected_action"] == "create_replacement"
    assert payload["resolved_at"] is not None
    assert payload["decision_reason"] == "金额核实无误，同意"
    # approve 不恢复 Run：Run 仍停在 waiting_for_approval。
    assert payload["agent_run_key"]
    assert payload["agent_run_status"] == "waiting_for_approval"

    risk = payload["risk"]
    assert risk["action"] == "create_replacement"
    assert risk["level"] == "high"
    assert risk["rule_code"] == "order_amount_above_approval_threshold"
    assert risk["requires_approval"] is True
    assert risk["order_key"] == HIGH_VALUE_ORDER_KEY
    assert risk["order_amount"] == str(SEEDED_ORDER_AMOUNT)
    assert risk["approval_threshold_amount"] == str(SEEDED_THRESHOLD)
    assert risk["policy_key"] == POLICY_KEY


def test_reject_endpoint_cancels_run():
    """reject 端点把审批请求标 REJECTED，并把 Run 转入 cancelled。"""
    approval_key = _start_high_risk_via_api()

    response = client.post(
        f"/approval-requests/{approval_key}/reject",
        json={"decision_reason": "拒绝"},
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "rejected"
    assert payload["agent_run_status"] == "cancelled"
    assert payload["resolved_at"] is not None
    assert payload["decision_reason"] == "拒绝"

    # 决策端点的响应必须与数据库真实状态一致。
    db = SessionLocal()
    try:
        approval = _approval(db, approval_key)
        assert approval.status is ApprovalRequestStatus.REJECTED
        run = approval.agent_run
        assert run.status is AgentRunStatus.CANCELLED
    finally:
        db.close()


def test_decision_endpoint_without_body_uses_no_reason():
    """不带请求体时 decision_reason 为空，但仍完成决策。"""
    approval_key = _start_high_risk_via_api()

    response = client.post(f"/approval-requests/{approval_key}/approve")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "approved"
    assert payload["decision_reason"] is None


def test_approve_endpoint_unknown_key_returns_404():
    """未知审批请求返回 404。"""
    for action in ("approve", "reject"):
        response = client.post(f"/approval-requests/approval-does-not-exist/{action}")
        assert response.status_code == 404


def test_same_decision_retry_via_api_is_idempotent():
    """同决策重试返回 200，且不刷新 resolved_at、不覆盖首次理由。"""
    approval_key = _start_high_risk_via_api()

    first = client.post(
        f"/approval-requests/{approval_key}/approve",
        json={"decision_reason": "第一次理由"},
    )
    assert first.status_code == 200
    first_resolved_at = first.json()["resolved_at"]

    second = client.post(
        f"/approval-requests/{approval_key}/approve",
        json={"decision_reason": "第二次不应覆盖"},
    )
    assert second.status_code == 200
    assert second.json()["resolved_at"] == first_resolved_at
    assert second.json()["decision_reason"] == "第一次理由"
    assert second.json()["status"] == "approved"


def test_conflicting_decision_via_api_returns_409():
    """只有已决策后的相反决策才是冲突：APPROVED 后 reject、REJECTED 后 approve。"""
    # 先从 PENDING approve 成功，再 reject 必须 409。
    approve_key = _start_high_risk_via_api()
    approved = client.post(
        f"/approval-requests/{approve_key}/approve", json={"decision_reason": "同意"}
    )
    assert approved.status_code == 200
    conflict = client.post(
        f"/approval-requests/{approve_key}/reject", json={"decision_reason": "反悔"}
    )
    assert conflict.status_code == 409

    # 反向：先从 PENDING reject 成功，再 approve 必须 409。
    reject_key = _start_high_risk_via_api()
    rejected = client.post(
        f"/approval-requests/{reject_key}/reject", json={"decision_reason": "拒"}
    )
    assert rejected.status_code == 200
    conflict2 = client.post(
        f"/approval-requests/{reject_key}/approve", json={"decision_reason": "翻案"}
    )
    assert conflict2.status_code == 409
