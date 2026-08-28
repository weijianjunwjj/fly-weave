"""T020 ApprovalRequest 持久化的确定性测试。

覆盖任务要求的验收点：

- 低风险 Golden Path 正常完成，且**不**产生任何审批请求；
- 高风险 Golden Path 产生一条 pending 审批请求，Run 停在 waiting_for_approval，
  且换货单不存在、工单仍为 open、update_ticket 从未执行；
- 审批请求保存的是风险触发那一刻的 snapshot：售后政策阈值事后被改动，历史
  审批原因不变（同时验证"重新求值"确实会给出不同结论，以证明快照不是重算）；
- 审批请求是真持久化：关闭 session、在全新 session 中仍能读回并恢复关联；
- 同一次 Run 的同一个受保护动作只有一条 pending 审批请求，且防重复由数据库级
  约束兜底，而不只是"先查后插"；
- 业务前置条件失败不产生审批请求，原失败语义不变；
- AgentRun 的 GET / POST 响应携带安全的 approval_request 与快照，不泄露内部
  数据库字段。

全部前置数据来自 ``seed_demo_data`` 的种子工单，高风险路径全程走 T011~T019 的
真实实现，测试不手工插入审批请求，也不伪造任何风险判断。
"""
from decimal import Decimal
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from agent_run_service import run_golden_path
from approval_service import (
    create_or_get_pending_approval,
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
    ApprovalRequest,
    Order,
    OrderStatus,
    ReplacementOrder,
    Ticket,
    TicketStatus,
)
from order_service import get_order
from orders import GetOrderRequest
from policy_service import lookup_replacement_policy
from replacement_service import create_replacement
from replacements import CreateReplacementRequest, CreateReplacementStatus
from risk import ProtectedAction, RiskLevel, RiskRuleCode
from risk_service import assess_persisted_replacement_risk
from seed_data import seed_demo_data
from ticket_service import UPDATE_TICKET_STEP_NAME

client = TestClient(app)

# 低风险场景：5 天前已送达、金额 299、有货 —— 唯一能走完整条成功路径的种子工单
LOW_RISK_TICKET_KEY = "ticket-demo-001"
LOW_RISK_ORDER_KEY = "order-demo-001"
# 高金额场景：业务条件 eligible，但金额 1299 超过政策阈值 500
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"
HIGH_VALUE_ORDER_KEY = "order-demo-002"
# 拒绝场景：购买已 60 天且商品无货，判定必然 blocked
REJECTED_TICKET_KEY = "ticket-demo-003"

POLICY_KEY = "policy-replacement-standard"
AVAILABLE_SKU = "SKU-EARBUD-PRO-01"

# 种子数据中高风险场景的真实金额与阈值，快照必须原样保留它们
SEEDED_ORDER_AMOUNT = Decimal("1299.00")
SEEDED_THRESHOLD = Decimal("500.00")

REASON = "耳机进水损坏，申请换货"
TEST_RUN_KEY_PREFIX = "agentrun-approval-"


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据并清理本模块残留 Run。

    ``seed_demo_data`` 会先清空 demo 工单，数据库级 ON DELETE CASCADE 随之清除
    本模块产生的 AgentRun、步骤、换货单与审批请求，因此测试可反复运行且互不干扰。
    个别测试会改动政策阈值或订单状态，同样由下一次播种重建。
    """
    _reset()
    yield
    _reset()


def _reset() -> None:
    db = SessionLocal()
    try:
        db.query(AgentRun).filter(
            AgentRun.business_key.like(f"{TEST_RUN_KEY_PREFIX}%")
        ).delete(synchronize_session=False)
        db.commit()
        seed_demo_data(db)
    finally:
        db.close()


# --------------------------------------------------------------------------
# 夹具构造：判定全部经由 T011~T015 的真实实现
# --------------------------------------------------------------------------


def _intent_outcome():
    raw = json.dumps(
        {
            "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT.value,
            "issue_summary": "耳机进水损坏，疑似质量问题",
            "requested_action": RequestedAction.REPLACEMENT.value,
            "confidence": 0.95,
        }
    )
    outcome = extract_intent(raw)
    assert outcome.status is IntentExtractionStatus.SUCCESS
    return outcome


def _eligible_decision(db, order_key: str = HIGH_VALUE_ORDER_KEY):
    """用真实实现从 seeded 数据产生一个 eligible 判定。"""
    intent = _intent_outcome()
    decision = decide_replacement(
        intent,
        lookup_replacement_policy(db, intent.intent),
        get_order(db, GetOrderRequest(order_key=order_key)),
        check_inventory(db, CheckInventoryRequest(product_sku=AVAILABLE_SKU)),
    )
    assert decision.status is ReplacementDecisionStatus.ELIGIBLE
    return decision


def _create_run(db, key: str, ticket_key: str = HIGH_VALUE_TICKET_KEY) -> AgentRun:
    """基于 seeded demo ticket 创建一个已持久化的 AgentRun。"""
    ticket_id = db.query(Ticket).filter(Ticket.business_key == ticket_key).one().id
    run = AgentRun(business_key=f"{TEST_RUN_KEY_PREFIX}{key}", ticket_id=ticket_id)
    db.add(run)
    db.commit()
    return run


def _request(order_key: str = HIGH_VALUE_ORDER_KEY) -> CreateReplacementRequest:
    return CreateReplacementRequest(
        order_key=order_key, product_sku=AVAILABLE_SKU, reason=REASON
    )


def _run_golden_path(ticket_key: str) -> str:
    """对给定种子工单执行一次真实的完整流程，返回本次 Run 的业务标识。"""
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.business_key == ticket_key).one()
        return run_golden_path(db, ticket).business_key
    finally:
        db.close()


def _approvals_for_run(db, run_key: str) -> list[ApprovalRequest]:
    return (
        db.query(ApprovalRequest)
        .join(AgentRun, ApprovalRequest.agent_run_id == AgentRun.id)
        .filter(AgentRun.business_key == run_key)
        .all()
    )


def _set_policy_threshold(amount: Decimal | None) -> None:
    """改动售后政策的人工审批金额阈值，用于验证历史快照不被重写。"""
    db = SessionLocal()
    try:
        policy = (
            db.query(AfterSalesPolicy)
            .filter(AfterSalesPolicy.business_key == POLICY_KEY)
            .one()
        )
        policy.approval_required_above_amount = amount
        db.commit()
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE A —— 低风险 Golden Path 不产生审批请求
# --------------------------------------------------------------------------


def test_safe_golden_path_completes_without_any_approval_request():
    """低风险案例正常创建换货单并完成，绝不产生审批请求。"""
    run_key = _run_golden_path(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = (
            db.query(AgentRun).filter(AgentRun.business_key == run_key).one()
        )

        # 成功路径的既有语义完全不变。
        assert agent_run.status is AgentRunStatus.COMPLETED
        assert agent_run.replacement is not None

        order = (
            db.query(Order).filter(Order.business_key == LOW_RISK_ORDER_KEY).one()
        )
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.order_id == order.id)
            .one_or_none()
            is not None
        )

        # 没有被风险门禁拦下，就没有任何审批请求。
        assert _approvals_for_run(db, run_key) == []
        assert db.query(ApprovalRequest).count() == 0
        assert get_pending_approval(db, agent_run) is None
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE B —— 高风险 Golden Path 产生 pending 审批请求且无业务副作用
# --------------------------------------------------------------------------


def test_high_risk_golden_path_persists_pending_approval_request():
    """高金额案例被风险门禁拦下，产生一条可追溯的 pending 审批请求。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = (
            db.query(AgentRun).filter(AgentRun.business_key == run_key).one()
        )

        # Run 停在等待审批：既没失败，也绝不能伪装成完成。
        assert agent_run.status is AgentRunStatus.WAITING_FOR_APPROVAL
        assert agent_run.completed_at is None
        assert agent_run.error_message is None

        approvals = _approvals_for_run(db, run_key)
        assert len(approvals) == 1
        approval = approvals[0]

        # 审批请求是独立业务实体，状态是 pending，而不是 error / failure / completed。
        assert approval.status is ApprovalRequestStatus.PENDING
        # pending 尚未有审批结果，不用占位时间伪造它。
        assert approval.resolved_at is None
        assert approval.created_at is not None

        # 明确回答"哪个 Run 的哪个受保护动作在等审批"。
        assert approval.agent_run_id == agent_run.id
        assert approval.agent_run.business_key == run_key
        assert approval.protected_action is ProtectedAction.CREATE_REPLACEMENT
        assert approval.business_key

        # 快照携带完整的风险依据。
        assert approval.risk_level is RiskLevel.HIGH
        assert (
            approval.risk_rule_code
            is RiskRuleCode.ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD
        )
        assert approval.risk_requires_approval is True
        assert approval.reason
        assert approval.risk_order_key == HIGH_VALUE_ORDER_KEY
        assert approval.risk_order_amount == SEEDED_ORDER_AMOUNT
        assert approval.risk_approval_threshold_amount == SEEDED_THRESHOLD
        assert approval.risk_policy_key == POLICY_KEY

        # --- 真正的安全边界：高风险路径没有任何业务副作用 ---
        order = (
            db.query(Order).filter(Order.business_key == HIGH_VALUE_ORDER_KEY).one()
        )
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.order_id == order.id)
            .one_or_none()
            is None
        )
        assert agent_run.replacement is None

        ticket = agent_run.ticket
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
        assert ticket.resolved_at is None
        assert ticket.replacement_id is None

        # 受保护动作没有成功，工单回写步骤根本不该启动。
        step_names = {step.name for step in agent_run.steps}
        assert UPDATE_TICKET_STEP_NAME not in step_names
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE C —— 快照是历史事实，不随政策变化而改写
# --------------------------------------------------------------------------


def test_snapshot_is_not_rewritten_when_policy_threshold_changes():
    """政策阈值事后被改高，pending 审批请求仍准确说明"当时为什么被拦截"。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)

    db = SessionLocal()
    try:
        approval = _approvals_for_run(db, run_key)[0]
        original_reason = approval.reason
        original_rule_code = approval.risk_rule_code
    finally:
        db.close()

    # 把阈值改到远高于订单金额：同一条规则此刻重新求值将不再命中。
    _set_policy_threshold(Decimal("99999.00"))

    db = SessionLocal()
    try:
        approval = _approvals_for_run(db, run_key)[0]

        # 快照逐项保持触发时刻的数据。
        assert approval.reason == original_reason
        assert approval.risk_rule_code is original_rule_code
        assert (
            approval.risk_rule_code
            is RiskRuleCode.ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD
        )
        assert approval.risk_level is RiskLevel.HIGH
        assert approval.risk_requires_approval is True
        assert approval.risk_order_amount == SEEDED_ORDER_AMOUNT
        assert approval.risk_approval_threshold_amount == SEEDED_THRESHOLD

        snapshot = risk_snapshot_of(approval)
        assert snapshot.approval_threshold_amount == SEEDED_THRESHOLD
        assert snapshot.reason == original_reason

        # 对照证明快照确实不是"此刻重算"：按当前政策重新求值已经不再要求审批。
        agent_run = (
            db.query(AgentRun).filter(AgentRun.business_key == run_key).one()
        )
        recomputed = assess_persisted_replacement_risk(db, agent_run)
        assert recomputed is not None
        assert recomputed.requires_approval is False
        assert recomputed.approval_threshold_amount == Decimal("99999.00")
    finally:
        db.close()


def test_api_risk_prefers_snapshot_over_recomputed_policy():
    """政策改变后，API 展示的仍是审批请求快照，而不是当前政策的重算结果。"""
    _run_golden_path(HIGH_VALUE_TICKET_KEY)
    _set_policy_threshold(Decimal("99999.00"))

    response = client.get(f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs/latest")
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "waiting_for_approval"

    # 兼容保留的 risk 字段，其数据源必须是快照。
    assert payload["risk"]["requires_approval"] is True
    assert payload["risk"]["approval_threshold_amount"] == str(SEEDED_THRESHOLD)
    assert payload["approval_request"]["risk"] == payload["risk"]


# --------------------------------------------------------------------------
# CASE D —— 真持久化：新 Session 可恢复
# --------------------------------------------------------------------------


def test_pending_approval_is_recoverable_in_a_brand_new_session():
    """关闭 session 后在全新 session 中仍能读回审批请求并恢复 Run 关联。"""
    run_key = _run_golden_path(HIGH_VALUE_TICKET_KEY)

    first = SessionLocal()
    try:
        assert len(_approvals_for_run(first, run_key)) == 1
        approval_key = _approvals_for_run(first, run_key)[0].business_key
    finally:
        # 显式关闭：后续断言不得依赖任何进程内缓存对象
        first.close()

    second = SessionLocal()
    try:
        approval = (
            second.query(ApprovalRequest)
            .filter(ApprovalRequest.business_key == approval_key)
            .one()
        )

        # pending 状态与快照都可从数据库恢复。
        assert approval.status is ApprovalRequestStatus.PENDING
        assert approval.resolved_at is None
        assert approval.risk_order_amount == SEEDED_ORDER_AMOUNT

        # Run 关联在新 session 中同样成立，两个方向都能走通。
        agent_run = (
            second.query(AgentRun).filter(AgentRun.business_key == run_key).one()
        )
        assert approval.agent_run.business_key == run_key
        assert [item.id for item in agent_run.approval_requests] == [approval.id]
        assert agent_run.status is AgentRunStatus.WAITING_FOR_APPROVAL

        # service 层的按-Run 查询也能恢复它。
        assert get_pending_approval(second, agent_run) is approval
    finally:
        second.close()


# --------------------------------------------------------------------------
# CASE E —— 幂等 / 防重复
# --------------------------------------------------------------------------


def test_repeated_gate_hits_keep_exactly_one_pending_approval():
    """同一次 Run 的同一个受保护动作重复被拦下，只有一条 pending 审批请求。"""
    db = SessionLocal()
    try:
        run = _create_run(db, "idempotent-001")
        decision = _eligible_decision(db)

        first = create_replacement(db, run, _request(), decision)
        second = create_replacement(db, run, _request(), decision)

        assert first.status is CreateReplacementStatus.APPROVAL_REQUIRED
        assert second.status is CreateReplacementStatus.APPROVAL_REQUIRED

        approvals = _approvals_for_run(db, run.business_key)
        assert len(approvals) == 1
        # 标识确定性派生，因此重复进入得到的是同一条记录。
        assert approvals[0].status is ApprovalRequestStatus.PENDING

        # 重复执行同样没有产生换货单。
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.agent_run_id == run.id)
            .one_or_none()
            is None
        )
    finally:
        db.close()


def test_service_returns_the_same_pending_request_on_repeated_calls():
    """service 层的 create-or-get 语义：重复调用返回同一条记录，不新增。"""
    db = SessionLocal()
    try:
        run = _create_run(db, "idempotent-002")
        decision = _eligible_decision(db)
        result = create_replacement(db, run, _request(), decision)
        assert result.risk is not None

        first = get_pending_approval(db, run)
        assert first is not None

        again = create_or_get_pending_approval(db, run, result.risk)
        db.commit()

        assert again.id == first.id
        assert len(_approvals_for_run(db, run.business_key)) == 1
    finally:
        db.close()


def test_database_constraint_rejects_a_second_pending_request():
    """防重复由数据库兜底，而不只是"先查后插"。

    绕过 service 直接插入第二条 pending 记录（使用不同的 business_key，以确保
    被拒绝的是 pending 唯一约束本身），数据库必须拒绝。并发下的两次插入因此
    不可能同时成功。
    """
    db = SessionLocal()
    try:
        run = _create_run(db, "idempotent-003")
        decision = _eligible_decision(db)
        result = create_replacement(db, run, _request(), decision)
        existing = get_pending_approval(db, run)
        assert existing is not None

        duplicate = ApprovalRequest(
            business_key=f"{existing.business_key}-duplicate",
            agent_run_id=run.id,
            protected_action=ProtectedAction.CREATE_REPLACEMENT,
            status=ApprovalRequestStatus.PENDING,
            risk_level=RiskLevel.HIGH,
            risk_rule_code=RiskRuleCode.ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD,
            risk_requires_approval=True,
            reason=result.risk.reason,
            risk_order_key=HIGH_VALUE_ORDER_KEY,
            risk_order_amount=SEEDED_ORDER_AMOUNT,
            risk_approval_threshold_amount=SEEDED_THRESHOLD,
            risk_policy_key=POLICY_KEY,
        )
        db.add(duplicate)

        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()

        assert len(_approvals_for_run(db, run.business_key)) == 1
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE F —— 业务前置条件失败不产生审批请求
# --------------------------------------------------------------------------


def test_precondition_failure_creates_no_approval_request():
    """前置条件在风险门禁之前就失败时，绝不产生审批请求，失败语义不变。"""
    db = SessionLocal()
    try:
        run = _create_run(db, "precondition-001")
        decision = _eligible_decision(db)

        # 判定之后、执行之前订单被取消：第 5 条前置条件失败，根本走不到风险门禁。
        order = (
            db.query(Order).filter(Order.business_key == HIGH_VALUE_ORDER_KEY).one()
        )
        order.status = OrderStatus.CANCELLED
        db.commit()

        result = create_replacement(db, run, _request(), decision)

        # 原失败语义完全不变。
        assert result.status is CreateReplacementStatus.ORDER_NOT_REPLACEABLE
        assert result.failure_reason is not None
        assert result.replacement is None

        # 失败不是等待审批：没有审批请求，Run 也没有进入等待审批。
        assert _approvals_for_run(db, run.business_key) == []
        assert db.query(ApprovalRequest).count() == 0
        db.refresh(run)
        assert run.status is not AgentRunStatus.WAITING_FOR_APPROVAL
    finally:
        db.close()


def test_blocked_decision_run_creates_no_approval_request():
    """判定阶段就被拒绝的 Run（超窗 + 无货）同样不产生审批请求。"""
    run_key = _run_golden_path(REJECTED_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = (
            db.query(AgentRun).filter(AgentRun.business_key == run_key).one()
        )
        assert agent_run.status is AgentRunStatus.FAILED
        assert agent_run.error_message is not None

        assert _approvals_for_run(db, run_key) == []
        assert db.query(ApprovalRequest).count() == 0
    finally:
        db.close()


# --------------------------------------------------------------------------
# CASE G —— API 表示
# --------------------------------------------------------------------------


EXPECTED_APPROVAL_FIELDS = {
    "approval_key",
    "status",
    "protected_action",
    "created_at",
    "resolved_at",
    "risk",
}


def test_start_endpoint_returns_approval_request_with_snapshot():
    """POST 启动高风险 Run 时，响应携带安全的 approval_request 与快照。"""
    response = client.post(f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs")
    assert response.status_code == 201

    payload = response.json()
    assert payload["status"] == "waiting_for_approval"
    assert payload["replacement"] is None

    approval = payload["approval_request"]
    assert approval is not None

    # 只暴露公开契约字段，不泄露自增主键或任何 ORM 内部字段。
    assert set(approval) == EXPECTED_APPROVAL_FIELDS
    assert "id" not in approval
    assert "agent_run_id" not in approval

    assert approval["approval_key"]
    assert approval["status"] == "pending"
    assert approval["protected_action"] == "create_replacement"
    assert approval["created_at"]
    assert approval["resolved_at"] is None

    # UI 直接消费快照，不需要重新推导规则。
    risk = approval["risk"]
    assert risk["action"] == "create_replacement"
    assert risk["level"] == "high"
    assert risk["rule_code"] == "order_amount_above_approval_threshold"
    assert risk["requires_approval"] is True
    assert risk["reason"]
    assert risk["order_key"] == HIGH_VALUE_ORDER_KEY
    assert risk["order_amount"] == str(SEEDED_ORDER_AMOUNT)
    assert risk["approval_threshold_amount"] == str(SEEDED_THRESHOLD)
    assert risk["policy_key"] == POLICY_KEY

    # 工单没有进入成功结果。
    assert payload["ticket_result"]["status"] == "open"
    assert payload["ticket_result"]["resolution"] is None


def test_latest_endpoint_returns_the_same_persisted_approval_request():
    """GET 最近一次 Run 时返回同一条已落库的审批请求。"""
    started = client.post(f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs")
    assert started.status_code == 201

    latest = client.get(f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs/latest")
    assert latest.status_code == 200

    payload = latest.json()
    assert payload["business_key"] == started.json()["business_key"]
    assert payload["status"] == "waiting_for_approval"
    assert payload["approval_request"] == started.json()["approval_request"]

    # API 返回的审批请求必须与数据库中真实存在的那一条一致。
    db = SessionLocal()
    try:
        approval = (
            db.query(ApprovalRequest)
            .filter(
                ApprovalRequest.business_key
                == payload["approval_request"]["approval_key"]
            )
            .one()
        )
        assert approval.status is ApprovalRequestStatus.PENDING
    finally:
        db.close()


def test_completed_run_response_carries_no_approval_request():
    """低风险 Run 完成后，响应里没有审批请求，也没有风险判断。"""
    response = client.post(f"/tickets/{LOW_RISK_TICKET_KEY}/agent-runs")
    assert response.status_code == 201

    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["approval_request"] is None
    assert payload["risk"] is None
