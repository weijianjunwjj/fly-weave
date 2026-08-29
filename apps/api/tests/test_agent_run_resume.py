"""T022 approved ApprovalRequest 驱动安全恢复执行闭环的确定性测试。

覆盖任务要求的验收点：

- PENDING / REJECTED 审批请求的 Resume 一律 409，且无任何业务副作用；
- APPROVED 审批请求可显式 Resume：真正创建换货单、回写工单、Run 完成；
- 同一 Run 重复 Resume 幂等：不产生第二张换货单、不重复工单回写；
- 并发 Resume 只有一次真正获得执行权，ReplacementOrder 恒为 1；
- 进程重启 / 全新 DB Session 后 Resume 依然成功（不依赖内存标志）；
- Approval 只能授权自己的 AgentRun / action / 订单 / 政策身份，任何一项不匹配
  都 fail closed；
- 政策阈值在审批后变化不重算历史 snapshot、不重新要求审批，matching approved
  请求仍能授权原动作；业务身份漂移则 fail closed；
- create_replacement / update_ticket 失败时 Run 不得伪装 completed，重试不重复
  换货单；
- 高风险 Golden Path 完整闭环 + 低风险无回归 + T019/T020/T021 不回归。

全部前置数据来自 ``seed_demo_data`` 的种子工单，高风险路径全程走 T011~T022 的
真实实现；测试只对个别边界（如伪造订单身份漂移、模拟 Tool 失败）做最小干预。
"""
import json
import threading
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from agent_run_service import (
    AgentRunNotFoundError,
    ApprovalNotFoundError,
    ResumeConflictError,
    resume_agent_run,
    run_golden_path,
)
from approval_decision_service import approve, reject
from approval_service import (
    approval_authorizes,
    get_pending_approval,
    risk_snapshot_of,
)
from approvals import ApprovalRequestStatus
from database import SessionLocal
from decision_service import decide_replacement
from decisions import ReplacementDecisionStatus
from intents import (
    IntentExtractionStatus,
    IntentType,
    RequestedAction,
    extract_intent,
)
from inventory import CheckInventoryRequest
from inventory_service import check_inventory
from main import app
from models import (
    AfterSalesPolicy,
    AgentRun,
    AgentRunStatus,
    AgentStepStatus,
    ApprovalRequest,
    Order,
    OrderStatus,
    ReplacementOrder,
    Ticket,
    TicketStatus,
    TicketResolution,
)
from order_service import get_order
from orders import GetOrderRequest
from policy_service import lookup_replacement_policy
from replacement_service import REPLACEMENT_STEP_NAME, create_replacement
from replacements import CreateReplacementRequest, CreateReplacementStatus
from risk import ProtectedAction, RiskLevel, RiskRuleCode
from seed_data import seed_demo_data
from ticket_service import UPDATE_TICKET_STEP_NAME

client = TestClient(app)

# 低风险场景：金额 299、有货、窗口内 —— 唯一能走完整条成功路径的种子工单
LOW_RISK_TICKET_KEY = "ticket-demo-001"
LOW_RISK_ORDER_KEY = "order-demo-001"
# 高金额场景：业务条件 eligible，但金额 1299 超过政策阈值 500
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"
HIGH_VALUE_ORDER_KEY = "order-demo-002"

AVAILABLE_SKU = "SKU-EARBUD-PRO-01"
POLICY_KEY = "policy-replacement-standard"
SEEDED_THRESHOLD = Decimal("500.00")

REASON = "右耳耳机无声，疑似质量问题，符合换货政策"


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据。

    ``seed_demo_data`` 会先清空 demo 工单，数据库级 ON DELETE CASCADE 随之清除本
    模块产生的 AgentRun、步骤、换货单与审批请求，因此测试可反复运行且互不干扰。
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
        approve(db, approval_key, "同意")
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


def _replacement_count(db, order_key: str) -> int:
    order = db.query(Order).filter(Order.business_key == order_key).one()
    return (
        db.query(ReplacementOrder)
        .filter(ReplacementOrder.order_id == order.id)
        .count()
    )


# --------------------------------------------------------------------------
# CASE G / CASE H 所需的判定构造：经 T011~T015 的真实实现，非手工伪造
# --------------------------------------------------------------------------


def _intent_outcome():
    raw = json.dumps(
        {
            "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT.value,
            "issue_summary": REASON,
            "requested_action": RequestedAction.REPLACEMENT.value,
            "confidence": 0.95,
        }
    )
    outcome = extract_intent(raw)
    assert outcome.status is IntentExtractionStatus.SUCCESS
    return outcome


def _eligible_decision(db, order_key: str):
    intent = _intent_outcome()
    decision = decide_replacement(
        intent,
        lookup_replacement_policy(db, intent.intent),
        get_order(db, GetOrderRequest(order_key=order_key)),
        check_inventory(db, CheckInventoryRequest(product_sku=AVAILABLE_SKU)),
    )
    assert decision.status is ReplacementDecisionStatus.ELIGIBLE
    return decision


def _request(order_key: str) -> CreateReplacementRequest:
    return CreateReplacementRequest(
        order_key=order_key, product_sku=AVAILABLE_SKU, reason=REASON
    )


# --------------------------------------------------------------------------
# CASE A / CASE B：PENDING / REJECTED 不可恢复，且无副作用
# --------------------------------------------------------------------------


def test_pending_approval_resume_returns_409_without_side_effects():
    """PENDING 审批请求的 Resume 返回 409，Run 仍等待审批，无任何业务副作用。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)

    db = SessionLocal()
    try:
        with pytest.raises(ResumeConflictError):
            resume_agent_run(db, run_key)
    finally:
        db.close()

    # API 层同样返回 409。
    response = client.post(f"/agent-runs/{run_key}/resume")
    assert response.status_code == 409

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.WAITING_FOR_APPROVAL
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 0
        assert run.ticket.resolution is None
    finally:
        db.close()


def test_rejected_approval_resume_returns_409_without_side_effects():
    """REJECTED 审批请求的 Resume 永久返回 409，Run 终止，无任何业务副作用。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        reject(db, approval_key, "超过授权金额上限，拒绝换货")
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(ResumeConflictError):
            resume_agent_run(db, run_key)
    finally:
        db.close()

    # 重复调用 / 全新 session 都稳定返回 409，且不产生换货单、不更新工单。
    response = client.post(f"/agent-runs/{run_key}/resume")
    assert response.status_code == 409

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.CANCELLED
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 0
        assert run.ticket.resolution is None
        assert run.ticket.status is TicketStatus.OPEN
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE C：APPROVED Resume 完成闭环
# --------------------------------------------------------------------------


def test_approved_resume_completes_the_run():
    """APPROVED Resume 真正创建换货单、回写工单，并把 Run 置为 completed。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    db = SessionLocal()
    try:
        run = resume_agent_run(db, run_key)
        assert run.status is AgentRunStatus.COMPLETED
    finally:
        db.close()

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.COMPLETED
        assert run.completed_at is not None
        assert run.error_message is None

        # 时间线七个步骤全部 completed：1-5 来自原 Golden Path，6-7 来自恢复执行
        steps = {step.name: step for step in run.steps}
        assert REPLACEMENT_STEP_NAME in steps
        assert UPDATE_TICKET_STEP_NAME in steps
        assert all(step.status is AgentStepStatus.COMPLETED for step in run.steps)

        # 换货单真实落库且只有一张
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 1

        # 工单进入最终成功状态
        ticket = run.ticket
        assert ticket.status is TicketStatus.RESOLVED
        assert ticket.resolution is TicketResolution.REPLACEMENT_CREATED
        assert ticket.resolution_replacement is not None
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE D：幂等 Resume
# --------------------------------------------------------------------------


def test_resume_retry_is_idempotent():
    """已 COMPLETED 的 Run 再次 Resume 幂等：不重复换货、不重复回写。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    db = SessionLocal()
    try:
        first = resume_agent_run(db, run_key)
        assert first.status is AgentRunStatus.COMPLETED
        resolved_at = first.ticket.resolved_at
    finally:
        db.close()

    db = SessionLocal()
    try:
        second = resume_agent_run(db, run_key)
        assert second.status is AgentRunStatus.COMPLETED
        assert second.ticket.resolved_at == resolved_at
    finally:
        db.close()

    db = SessionLocal()
    try:
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 1
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.COMPLETED
        # 工单仍只引用那一张换货单
        assert run.ticket.resolution_replacement is not None
        assert run.ticket.status is TicketStatus.RESOLVED
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE E：并发 Resume 只有一次 protected action
# --------------------------------------------------------------------------


def test_concurrent_resume_executes_protected_action_once():
    """两个并发 Resume 只有一个真正获得执行权，ReplacementOrder 恒为 1。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    outcomes: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def do_resume() -> None:
        db = SessionLocal()
        try:
            barrier.wait(timeout=10)
            try:
                resume_agent_run(db, run_key)
                with lock:
                    outcomes.append("ok")
            except ResumeConflictError:
                with lock:
                    outcomes.append("conflict")
            except Exception as exc:  # noqa: BLE001 - 让意外失败显式暴露
                with lock:
                    outcomes.append(f"error:{repr(exc)}")
        finally:
            db.close()

    first = threading.Thread(target=do_resume)
    second = threading.Thread(target=do_resume)
    first.start()
    second.start()
    first.join(timeout=30)
    second.join(timeout=30)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(outcomes) == 2, outcomes
    assert not any(o.startswith("error") for o in outcomes), outcomes
    # 至少一个真正执行（另一个要么 409 冲突，要么幂等返回已 completed）
    assert any(o == "ok" for o in outcomes), outcomes

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.COMPLETED
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE F：重启 / 全新 DB Session 后 Resume
# --------------------------------------------------------------------------


def test_resume_works_after_new_db_session():
    """审批在全新 session 中仍可被识别并完成恢复，不依赖内存中的临时标志。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    first = SessionLocal()
    try:
        approve(first, approval_key, "同意")
    finally:
        first.close()

    second = SessionLocal()
    try:
        run = resume_agent_run(second, run_key)
        assert run.status is AgentRunStatus.COMPLETED
    finally:
        second.close()

    third = SessionLocal()
    try:
        run = _run(third, run_key)
        assert run.status is AgentRunStatus.COMPLETED
        assert _replacement_count(third, HIGH_VALUE_ORDER_KEY) == 1
        assert run.ticket.status is TicketStatus.RESOLVED
    finally:
        third.close()


# --------------------------------------------------------------------------
# CASE G：Approval 属于 Run A，不得用于 Run B
# --------------------------------------------------------------------------


def test_approval_cannot_authorize_a_different_agent_run():
    """一条 APPROVED 审批请求不能授权另一个 AgentRun 执行受保护动作。"""
    run_a_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_a_key)
    _approve(approval_key)

    db = SessionLocal()
    try:
        approval = _approval(db, approval_key)
        run_a = _run(db, run_a_key)
        run_b = AgentRun(
            business_key="agentrun-resume-caseg", ticket_id=run_a.ticket_id
        )
        db.add(run_b)
        db.commit()

        # 绑定函数直接拒绝：不同 Run。
        assert approval_authorizes(approval, run_b, HIGH_VALUE_ORDER_KEY, POLICY_KEY) is False

        result = create_replacement(
            db, run_b, _request(HIGH_VALUE_ORDER_KEY), _eligible_decision(db, HIGH_VALUE_ORDER_KEY),
            authorization=approval,
        )
        assert result.status is CreateReplacementStatus.AUTHORIZATION_MISMATCH
        assert result.replacement is None
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 0
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE H：action / context 不匹配 fail closed
# --------------------------------------------------------------------------


def test_approval_binding_rejects_order_and_policy_mismatch():
    """审批与当前执行的订单 / 政策身份不匹配时 fail closed，绝不执行。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        approval = _approval(db, f"approval-{run_key}-create_replacement")

        # 绑定函数逐项核对：完整匹配才授权，任一身份对不上都拒绝。
        assert approval_authorizes(approval, run, HIGH_VALUE_ORDER_KEY, POLICY_KEY) is True
        assert approval_authorizes(approval, run, "order-someone-else", POLICY_KEY) is False
        assert approval_authorizes(approval, run, HIGH_VALUE_ORDER_KEY, "policy-someone-else") is False
    finally:
        db.close()

    # 集成层：给 create_replacement 一条订单身份不同的 APPROVED 审批，必须 AUTHORIZATION_MISMATCH。
    db = SessionLocal()
    try:
        run = _run(db, run_key)
        forged = ApprovalRequest(
            business_key="approval-forged-other-order",
            agent_run_id=run.id,
            protected_action=ProtectedAction.CREATE_REPLACEMENT,
            status=ApprovalRequestStatus.APPROVED,
            risk_level=RiskLevel.HIGH,
            risk_rule_code=RiskRuleCode.ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD,
            risk_requires_approval=True,
            reason="伪造：属于另一张订单的审批",
            risk_order_key="order-someone-else",
            risk_order_amount=Decimal("1299.00"),
            risk_approval_threshold_amount=SEEDED_THRESHOLD,
            risk_policy_key=POLICY_KEY,
        )
        db.add(forged)
        db.commit()

        result = create_replacement(
            db, run, _request(HIGH_VALUE_ORDER_KEY), _eligible_decision(db, HIGH_VALUE_ORDER_KEY),
            authorization=forged,
        )
        assert result.status is CreateReplacementStatus.AUTHORIZATION_MISMATCH
        assert result.replacement is None
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 0
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE I：政策阈值在审批后变化，不否定历史审批
# --------------------------------------------------------------------------


def test_policy_threshold_change_does_not_invalidate_historical_approval():
    """审批后调低阈值：当前风险仍 HIGH，但历史 APPROVED 仍授权原动作，snapshot 不变。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)
    _approve(approval_key)

    db = SessionLocal()
    try:
        before = risk_snapshot_of(_approval(db, approval_key))
        assert before.approval_threshold_amount == SEEDED_THRESHOLD
    finally:
        db.close()

    # 把阈值调低到 100：当前执行时刻风险仍是 HIGH（1299 > 100），但不应重新要求审批。
    db = SessionLocal()
    try:
        policy = db.query(AfterSalesPolicy).filter(AfterSalesPolicy.business_key == POLICY_KEY).one()
        policy.approval_required_above_amount = Decimal("100.00")
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        run = resume_agent_run(db, run_key)
        assert run.status is AgentRunStatus.COMPLETED
    finally:
        db.close()

    db = SessionLocal()
    try:
        approval = _approval(db, approval_key)
        # 历史 snapshot 不因阈值变化而重算
        assert risk_snapshot_of(approval) == before
        assert approval.risk_approval_threshold_amount == SEEDED_THRESHOLD
        assert approval.status is ApprovalRequestStatus.APPROVED
        # 没有第二个审批请求，换货单只有一张
        assert db.query(ApprovalRequest).count() == 1
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE J：业务身份漂移 fail closed
# --------------------------------------------------------------------------


def test_business_identity_drift_fails_closed():
    """审批后 Run 的工单改挂到另一张订单时，Resume fail closed，无副作用。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        other_order = db.query(Order).filter(Order.business_key == LOW_RISK_ORDER_KEY).one()
        run.ticket.order_id = other_order.id
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(ResumeConflictError):
            resume_agent_run(db, run_key)
    finally:
        db.close()

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.WAITING_FOR_APPROVAL
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 0
        assert run.ticket.resolution is None
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE K：create_replacement 失败不得完成
# --------------------------------------------------------------------------


def test_create_replacement_failure_does_not_complete():
    """受保护动作执行失败时 Run 置为 FAILED，不产生换货单、不错误更新工单。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    # 审批后把订单取消：判定变为 blocked，create_replacement 结构化失败。
    db = SessionLocal()
    try:
        order = db.query(Order).filter(Order.business_key == HIGH_VALUE_ORDER_KEY).one()
        order.status = OrderStatus.CANCELLED
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        run = resume_agent_run(db, run_key)
        assert run.status is AgentRunStatus.FAILED
        assert run.status is not AgentRunStatus.COMPLETED
    finally:
        db.close()

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.FAILED
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 0
        assert run.ticket.status is TicketStatus.OPEN
        assert run.ticket.resolution is None
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE L：update_ticket 失败不伪造 completed，重试不重复 Replacement
# --------------------------------------------------------------------------


def test_update_ticket_failure_does_not_fake_completion(monkeypatch):
    """工单回写失败时 Run 不 completed，重试不重复创建换货单。"""
    import agent_run_service
    from ticket_service import update_ticket as real_update_ticket
    from tickets import UpdateTicketResult, UpdateTicketStatus

    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _approve(_pending_approval_key(run_key))

    def failing_update_ticket(db, agent_run, request):
        return UpdateTicketResult(
            status=UpdateTicketStatus.PERSISTENCE_FAILED,
            failure_reason="模拟工单回写失败",
        )

    monkeypatch.setattr(agent_run_service, "update_ticket", failing_update_ticket)

    db = SessionLocal()
    try:
        run = resume_agent_run(db, run_key)
        assert run.status is AgentRunStatus.FAILED
        assert run.status is not AgentRunStatus.COMPLETED
    finally:
        db.close()

    # 换货单已真实落库，但工单没有被错误标记成功。
    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.FAILED
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 1
        assert run.ticket.status is TicketStatus.OPEN
        assert run.ticket.resolution is None
    finally:
        db.close()

    # 重试：Run 已 FAILED，恢复真实 update_ticket 后再次 Resume 被 409 拒绝，
    # 且换货单仍只有一张（不重复副作用）。
    monkeypatch.setattr(agent_run_service, "update_ticket", real_update_ticket)
    db = SessionLocal()
    try:
        with pytest.raises(ResumeConflictError):
            resume_agent_run(db, run_key)
    finally:
        db.close()

    db = SessionLocal()
    try:
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE M：完整高风险 Golden Path E2E
# --------------------------------------------------------------------------


def test_full_high_risk_golden_path_e2e():
    """高风险 seeded 场景走完整闭环：start → 等待审批 → approve → resume → completed。"""
    start = client.post(f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs")
    assert start.status_code == 201
    payload = start.json()
    assert payload["status"] == "waiting_for_approval"
    run_key = payload["business_key"]
    approval_key = payload["approval_request"]["approval_key"]

    approved = client.post(
        f"/approval-requests/{approval_key}/approve",
        json={"decision_reason": "金额核实无误，同意换货"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    resume = client.post(f"/agent-runs/{run_key}/resume")
    assert resume.status_code == 200
    body = resume.json()

    # 响应覆盖任务要求的字段，不泄露内部 DB 字段
    assert body["agent_run"]["business_key"] == run_key
    assert body["agent_run"]["status"] == "completed"
    assert body["agent_run"]["replacement"]["business_key"] == f"replacement-{HIGH_VALUE_ORDER_KEY}"
    assert body["agent_run"]["ticket_result"]["status"] == "resolved"
    assert body["agent_run"]["ticket_result"]["resolution"] == "replacement_created"
    assert body["approval"]["approval_key"] == approval_key
    assert body["approval"]["status"] == "approved"
    assert body["approval"]["protected_action"] == "create_replacement"

    db = SessionLocal()
    try:
        approval = _approval(db, approval_key)
        assert approval.status is ApprovalRequestStatus.APPROVED
        assert db.query(ApprovalRequest).count() == 1
        assert _replacement_count(db, HIGH_VALUE_ORDER_KEY) == 1
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.COMPLETED
        assert run.ticket.status is TicketStatus.RESOLVED
        assert run.ticket.resolution is TicketResolution.REPLACEMENT_CREATED
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE N：低风险 Golden Path 无回归
# --------------------------------------------------------------------------


def test_low_risk_golden_path_no_regression():
    """低风险 Golden Path 仍直接完成，无审批请求；对其 Resume 幂等且 approval 为空。"""
    response = client.post(f"/tickets/{LOW_RISK_TICKET_KEY}/agent-runs")
    assert response.status_code == 201
    assert response.json()["status"] == "completed"
    run_key = response.json()["business_key"]

    resume = client.post(f"/agent-runs/{run_key}/resume")
    assert resume.status_code == 200
    assert resume.json()["agent_run"]["status"] == "completed"
    assert resume.json()["approval"] is None

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.COMPLETED
        assert get_pending_approval(db, run) is None
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE O：T019 / T020 / T021 回归
# --------------------------------------------------------------------------


def test_t019_t020_t021_regression():
    """高风险仍等待审批并产生 pending 请求，reject 终止 Run，低风险直接完成。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)
    approval_key = _pending_approval_key(run_key)

    db = SessionLocal()
    try:
        run = _run(db, run_key)
        assert run.status is AgentRunStatus.WAITING_FOR_APPROVAL
        approval = _approval(db, approval_key)
        assert approval.status is ApprovalRequestStatus.PENDING
    finally:
        db.close()

    db = SessionLocal()
    try:
        reject(db, approval_key, "拒绝")
    finally:
        db.close()

    db = SessionLocal()
    try:
        assert _run(db, run_key).status is AgentRunStatus.CANCELLED
    finally:
        db.close()

    low_run_key = _run_golden_path(LOW_RISK_TICKET_KEY)
    db = SessionLocal()
    try:
        run = _run(db, low_run_key)
        assert run.status is AgentRunStatus.COMPLETED
        assert get_pending_approval(db, run) is None
    finally:
        db.close()


def test_unknown_run_resume_returns_404():
    """不存在的 Run 返回 404（由 endpoint 映射），而不是 500 或伪造结果。"""
    response = client.post("/agent-runs/run-does-not-exist/resume")
    assert response.status_code == 404
