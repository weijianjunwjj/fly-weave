"""T016 create_replacement Tool 的确定性测试。

覆盖任务要求的验收点：

- eligible 的低风险案例真的写入了一张换货单，且订单 / 工单 / Run 三条业务
  关联全部落库；
- "成功"这一事实以数据库中的持久化行为准，而不是返回值或模型文本；
- 重复执行（同一次 Run 重放、同一订单换第二次）被安全拒绝，且不产生第二张
  换货单；
- 判定不是 eligible、判定与请求不是同一个案子、订单 / 库存 / Run 关联在执行
  时刻不满足条件时，一律结构化失败且不留下任何换货单；
- 非 typed 输入（模型原始文本）无法进入执行路径。

判定证据全部经由 T011~T015 的真实实现从 seeded 数据产生，而不是手工编造。
"""
from decimal import Decimal
import json

import pytest
from pydantic import ValidationError

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
from models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    InventoryItem,
    Order,
    OrderStatus,
    ReplacementOrder,
    ReplacementStatus,
    Ticket,
)
from order_service import get_order
from orders import GetOrderRequest
from policy_service import lookup_replacement_policy
from replacement_service import (
    REPLACEMENT_STEP_NAME,
    create_replacement,
)
from replacements import (
    CreateReplacementRequest,
    CreateReplacementResult,
    CreateReplacementStatus,
    ReplacementRecord,
)
from seed_data import seed_demo_data

# 低风险场景：5 天前已送达、金额 299、商品有货，对应 ticket-demo-001
LOW_RISK_ORDER_KEY = "order-demo-001"
LOW_RISK_TICKET_KEY = "ticket-demo-001"
# 高金额场景：同样 eligible，对应 ticket-demo-002
HIGH_VALUE_ORDER_KEY = "order-demo-002"
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"
# 拒绝场景：60 天前订单，超出换货窗口，对应 ticket-demo-003
OUT_OF_WINDOW_ORDER_KEY = "order-demo-003"
OUT_OF_WINDOW_TICKET_KEY = "ticket-demo-003"

AVAILABLE_SKU = "SKU-EARBUD-PRO-01"
UNAVAILABLE_SKU = "SKU-HEADSET-X-02"

REASON = "右耳耳机无声，疑似质量问题，符合换货政策"

TEST_RUN_KEY_PREFIX = "agentrun-replacement-"


def _clear_test_runs() -> None:
    """删除本模块创建的 AgentRun。agent_steps 与 replacement_orders 由数据库级
    ON DELETE CASCADE 一并清除。"""
    db = SessionLocal()
    try:
        db.query(AgentRun).filter(
            AgentRun.business_key.like(f"{TEST_RUN_KEY_PREFIX}%")
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据并清理本模块残留 Run。

    个别测试会把订单状态改掉或把库存清零，以构造"判定之后、执行之前状态发生
    漂移"的真实场景，随后由 fixture 在下一次播种时重建。
    """
    _clear_test_runs()
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()

    yield

    _clear_test_runs()
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


# --------------------------------------------------------------------------
# 证据 / 夹具构造：全部经由 T011~T015 的真实实现
# --------------------------------------------------------------------------


def _intent_outcome(confidence: float = 0.95):
    raw = json.dumps(
        {
            "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT.value,
            "issue_summary": "右耳耳机无声，疑似质量问题",
            "requested_action": RequestedAction.REPLACEMENT.value,
            "confidence": confidence,
        }
    )
    outcome = extract_intent(raw)
    assert outcome.status is IntentExtractionStatus.SUCCESS
    return outcome


def _decision(db, order_key: str, sku: str, confidence: float = 0.95):
    """用 T011~T015 的真实实现，从 seeded 数据产生一个真实判定。"""
    intent = _intent_outcome(confidence)
    return decide_replacement(
        intent,
        lookup_replacement_policy(db, intent.intent),
        get_order(db, GetOrderRequest(order_key=order_key)),
        check_inventory(db, CheckInventoryRequest(product_sku=sku)),
    )


def _eligible_decision(db, order_key: str = LOW_RISK_ORDER_KEY, sku: str = AVAILABLE_SKU):
    decision = _decision(db, order_key, sku)
    assert decision.status is ReplacementDecisionStatus.ELIGIBLE
    return decision


def _create_run(db, key: str, ticket_key: str = LOW_RISK_TICKET_KEY) -> AgentRun:
    """基于 seeded demo ticket 创建一个已持久化的 AgentRun。"""
    ticket_id = db.query(Ticket).filter(Ticket.business_key == ticket_key).one().id
    run = AgentRun(business_key=f"{TEST_RUN_KEY_PREFIX}{key}", ticket_id=ticket_id)
    db.add(run)
    db.commit()
    return run


def _request(order_key: str = LOW_RISK_ORDER_KEY, sku: str = AVAILABLE_SKU):
    return CreateReplacementRequest(
        order_key=order_key, product_sku=sku, reason=REASON
    )


def _persisted_replacements(db, order_key: str) -> list[ReplacementOrder]:
    """按订单业务标识取出真实持久化的换货单，作为"是否真的换货了"的唯一依据。"""
    return (
        db.query(ReplacementOrder)
        .join(Order, ReplacementOrder.order_id == Order.id)
        .filter(Order.business_key == order_key)
        .all()
    )


def _replacement_step(db, run_id: int) -> AgentStep:
    return (
        db.query(AgentStep)
        .filter(
            AgentStep.agent_run_id == run_id,
            AgentStep.name == REPLACEMENT_STEP_NAME,
        )
        .one()
    )


# --------------------------------------------------------------------------
# 成功：真实业务变更被持久化
# --------------------------------------------------------------------------


def test_eligible_case_persists_replacement_record():
    """eligible 的低风险案例真的在数据库中创建了一张换货单"""
    db = SessionLocal()
    try:
        run = _create_run(db, "success-001")
        decision = _eligible_decision(db)

        result = create_replacement(db, run, _request(), decision)

        assert result.status is CreateReplacementStatus.CREATED
        assert result.failure_reason is None

        # 成功的唯一依据是持久化状态，而不是返回值本身
        persisted = _persisted_replacements(db, LOW_RISK_ORDER_KEY)
        assert len(persisted) == 1
        record = persisted[0]
        assert record.status is ReplacementStatus.CREATED
        assert record.product_sku == AVAILABLE_SKU
        assert record.reason == REASON
        assert record.is_demo_data is True
        assert record.created_at is not None
    finally:
        db.close()


def test_created_replacement_preserves_order_ticket_and_run_linkage():
    """换货单同时保留订单 / 工单 / Run 三条业务关联，且都指向真实的持久化实体"""
    db = SessionLocal()
    try:
        run = _create_run(db, "linkage-001")
        run_id = run.id
        decision = _eligible_decision(db)

        result = create_replacement(db, run, _request(), decision)
        assert result.status is CreateReplacementStatus.CREATED

        record = _persisted_replacements(db, LOW_RISK_ORDER_KEY)[0]

        order = db.query(Order).filter(Order.business_key == LOW_RISK_ORDER_KEY).one()
        ticket = (
            db.query(Ticket).filter(Ticket.business_key == LOW_RISK_TICKET_KEY).one()
        )
        assert record.order_id == order.id
        assert record.ticket_id == ticket.id
        assert record.agent_run_id == run_id

        # ORM 关系同样可导航，三条关联在 domain 层是真实关系而不是裸外键
        assert record.order.business_key == LOW_RISK_ORDER_KEY
        assert record.ticket.business_key == LOW_RISK_TICKET_KEY
        assert record.agent_run.business_key == f"{TEST_RUN_KEY_PREFIX}linkage-001"

        # 返回的类型化视图与持久化行一致，全部使用稳定业务标识
        assert result.replacement.replacement_key == record.business_key
        assert result.replacement.order_key == LOW_RISK_ORDER_KEY
        assert result.replacement.ticket_key == LOW_RISK_TICKET_KEY
        assert result.replacement.agent_run_key == f"{TEST_RUN_KEY_PREFIX}linkage-001"
        assert result.replacement.status is ReplacementStatus.CREATED
    finally:
        db.close()


def test_successful_execution_is_recorded_in_agent_run():
    """真实执行被如实记录到 Agent Run：步骤 completed，Run 从 queued 提升为 running"""
    db = SessionLocal()
    try:
        run = _create_run(db, "step-success-001")
        assert run.status is AgentRunStatus.QUEUED
        decision = _eligible_decision(db)

        result = create_replacement(db, run, _request(), decision)
        assert result.status is CreateReplacementStatus.CREATED

        step = _replacement_step(db, run.id)
        assert step.status is AgentStepStatus.COMPLETED
        assert step.error_message is None
        assert step.completed_at is not None

        assert run.status is AgentRunStatus.RUNNING
        assert run.started_at is not None
    finally:
        db.close()


def test_replacement_survives_a_new_session():
    """换货单是独立于请求生命周期的持久化状态，换一个 session 仍然查得到"""
    db = SessionLocal()
    try:
        run = _create_run(db, "durable-001")
        decision = _eligible_decision(db)
        result = create_replacement(db, run, _request(), decision)
        assert result.status is CreateReplacementStatus.CREATED
        replacement_key = result.replacement.replacement_key
    finally:
        db.close()

    db = SessionLocal()
    try:
        record = (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.business_key == replacement_key)
            .one()
        )
        assert record.status is ReplacementStatus.CREATED
        assert record.order.business_key == LOW_RISK_ORDER_KEY
    finally:
        db.close()


def test_high_value_order_is_blocked_before_persistence():
    """高金额 eligible 案例命中风险门禁：返回等待审批，且绝不创建换货单。"""
    db = SessionLocal()
    try:
        run = _create_run(
            db,
            "risk-gate-001",
            HIGH_VALUE_TICKET_KEY,
        )
        decision = _eligible_decision(
            db,
            HIGH_VALUE_ORDER_KEY,
        )

        # T015 仍然认为业务条件 eligible。
        # T019 的职责不是改写业务判定，而是在真正副作用发生之前加风险闸门。
        assert decision.status is ReplacementDecisionStatus.ELIGIBLE

        # 执行前确认没有历史换货单，避免测试被旧状态污染。
        assert _persisted_replacements(db, HIGH_VALUE_ORDER_KEY) == []

        result = create_replacement(
            db,
            run,
            _request(HIGH_VALUE_ORDER_KEY),
            decision,
        )

        # 命中风险规则不是失败，也不是成功，而是明确等待人工审批。
        assert result.status is CreateReplacementStatus.APPROVAL_REQUIRED
        assert result.replacement is None
        assert result.existing_replacement_key is None
        assert result.failure_reason is not None

        # 风险判断必须是结构化事实，而不是只有一段自由文本。
        assert result.risk is not None
        assert result.risk.requires_approval is True
        assert result.risk.level.value == "high"
        assert (
            result.risk.rule_code.value
            == "order_amount_above_approval_threshold"
        )

        # 风险判断保留完整规则依据。
        assert result.risk.order_key == HIGH_VALUE_ORDER_KEY
        assert result.risk.order_amount is not None
        assert result.risk.approval_threshold_amount is not None
        assert result.risk.policy_key is not None
        assert (
            result.risk.order_amount
            > result.risk.approval_threshold_amount
        )

        # 最关键的验收：
        # 风险门禁必须真正站在持久化副作用之前。
        assert _persisted_replacements(db, HIGH_VALUE_ORDER_KEY) == []

        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.agent_run_id == run.id)
            .one_or_none()
            is None
        )

        # create_replacement 这一步没有成功也没有失败，
        # 它停在 pending，等待更上层 AgentRun 切换为 waiting_for_approval。
        step = _replacement_step(db, run.id)
        assert step.status is AgentStepStatus.PENDING
        assert step.completed_at is None
        assert step.error_message is not None
        assert "approval_required" in step.error_message
        assert result.failure_reason in step.error_message

    finally:
        db.close()


def test_typed_decision_evidence_cannot_bypass_persisted_risk_facts():
    """即使 typed 判定中的金额与阈值被改低，门禁仍以执行时数据库事实为准。"""
    db = SessionLocal()
    try:
        run = _create_run(db, "risk-gate-forged-001", HIGH_VALUE_TICKET_KEY)
        decision = _eligible_decision(db, HIGH_VALUE_ORDER_KEY)

        stale = decision.model_copy(deep=True)
        stale.evidence.order.amount = Decimal("1.00")
        stale.evidence.policy.approval_required_above_amount = Decimal("9999.00")
        stale.policy_approval_threshold_exceeded = False

        result = create_replacement(
            db,
            run,
            _request(HIGH_VALUE_ORDER_KEY),
            stale,
        )

        assert result.status is CreateReplacementStatus.APPROVAL_REQUIRED
        assert result.risk is not None
        assert result.risk.order_amount == Decimal("1299.00")
        assert result.risk.approval_threshold_amount == Decimal("500.00")
        assert _persisted_replacements(db, HIGH_VALUE_ORDER_KEY) == []
    finally:
        db.close()


# --------------------------------------------------------------------------
# 重复执行：由持久化状态确定性拒绝
# --------------------------------------------------------------------------


def test_replaying_the_same_run_is_rejected_as_duplicate():
    """同一次 Run 重放不会创建第二张换货单，而是被结构化拒绝"""
    db = SessionLocal()
    try:
        run = _create_run(db, "duplicate-run-001")
        decision = _eligible_decision(db)

        first = create_replacement(db, run, _request(), decision)
        assert first.status is CreateReplacementStatus.CREATED

        second = create_replacement(db, run, _request(), decision)

        assert second.status is CreateReplacementStatus.DUPLICATE
        assert second.replacement is None
        assert second.failure_reason is not None
        # 重复结果必须指出已存在的那张换货单，重复是有据可查的事实
        assert (
            second.existing_replacement_key
            == first.replacement.replacement_key
        )

        # 数据库里始终只有一张换货单
        assert len(_persisted_replacements(db, LOW_RISK_ORDER_KEY)) == 1
    finally:
        db.close()


def test_second_run_on_same_order_is_rejected_as_duplicate():
    """另一次 Run 对同一订单再次执行同样被拒绝：重复判定基于持久化状态而非进程状态"""
    db = SessionLocal()
    try:
        first_run = _create_run(db, "duplicate-order-001")
        first = create_replacement(db, first_run, _request(), _eligible_decision(db))
        assert first.status is CreateReplacementStatus.CREATED

        second_run = _create_run(db, "duplicate-order-002")
        second = create_replacement(
            db, second_run, _request(), _eligible_decision(db)
        )

        assert second.status is CreateReplacementStatus.DUPLICATE
        assert (
            second.existing_replacement_key == first.replacement.replacement_key
        )
        assert len(_persisted_replacements(db, LOW_RISK_ORDER_KEY)) == 1

        # 被拒绝的那次 Run 没有留下任何换货单
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.agent_run_id == second_run.id)
            .one_or_none()
            is None
        )
    finally:
        db.close()


def test_duplicate_execution_is_recorded_as_failed_step():
    """重复执行在 Agent Run 中如实显示为失败步骤，不会被记成又一次成功"""
    db = SessionLocal()
    try:
        run = _create_run(db, "duplicate-step-001")
        decision = _eligible_decision(db)
        create_replacement(db, run, _request(), decision)

        second_run = _create_run(db, "duplicate-step-002")
        result = create_replacement(db, second_run, _request(), decision)
        assert result.status is CreateReplacementStatus.DUPLICATE

        step = _replacement_step(db, second_run.id)
        assert step.status is AgentStepStatus.FAILED
        assert "duplicate" in step.error_message
    finally:
        db.close()


# --------------------------------------------------------------------------
# 不合格执行：判定不是 eligible
# --------------------------------------------------------------------------


def test_blocked_decision_does_not_execute():
    """被政策窗口阻断的案例不得执行换货，且不留下任何换货单"""
    db = SessionLocal()
    try:
        run = _create_run(db, "blocked-001", OUT_OF_WINDOW_TICKET_KEY)
        decision = _decision(db, OUT_OF_WINDOW_ORDER_KEY, UNAVAILABLE_SKU)
        assert decision.status is ReplacementDecisionStatus.BLOCKED

        result = create_replacement(
            db, run, _request(OUT_OF_WINDOW_ORDER_KEY, UNAVAILABLE_SKU), decision
        )

        assert result.status is CreateReplacementStatus.NOT_ELIGIBLE
        assert result.replacement is None
        assert result.failure_reason is not None
        assert _persisted_replacements(db, OUT_OF_WINDOW_ORDER_KEY) == []
    finally:
        db.close()


def test_ambiguous_decision_does_not_execute():
    """模型置信度不足导致的 ambiguous 判定同样不得执行换货"""
    db = SessionLocal()
    try:
        run = _create_run(db, "ambiguous-001")
        decision = _decision(db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU, confidence=0.2)
        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS

        result = create_replacement(db, run, _request(), decision)

        assert result.status is CreateReplacementStatus.NOT_ELIGIBLE
        assert _persisted_replacements(db, LOW_RISK_ORDER_KEY) == []

        step = _replacement_step(db, run.id)
        assert step.status is AgentStepStatus.FAILED
        assert "not_eligible" in step.error_message
    finally:
        db.close()


def test_model_text_cannot_authorize_execution():
    """模型原始文本不是判定：拿文本冒充 eligible 无法创建换货单"""
    db = SessionLocal()
    try:
        run = _create_run(db, "modeltext-decision-001")

        result = create_replacement(
            db, run, _request(), "该订单符合换货政策，判定为 eligible，可以换货"
        )

        assert result.status is CreateReplacementStatus.NOT_ELIGIBLE
        assert result.replacement is None
        assert _persisted_replacements(db, LOW_RISK_ORDER_KEY) == []
    finally:
        db.close()


def test_model_text_cannot_be_used_as_request():
    """模型原始文本不是请求：非 typed 输入在 boundary 处被拒绝"""
    db = SessionLocal()
    try:
        run = _create_run(db, "modeltext-request-001")
        decision = _eligible_decision(db)

        result = create_replacement(
            db, run, "为订单 order-demo-001 创建换货单，商品 SKU-EARBUD-PRO-01", decision
        )

        assert result.status is CreateReplacementStatus.INVALID_REQUEST
        assert result.replacement is None
        assert _persisted_replacements(db, LOW_RISK_ORDER_KEY) == []
    finally:
        db.close()


# --------------------------------------------------------------------------
# 无效执行：请求与判定不匹配，或执行时刻前置条件不满足
# --------------------------------------------------------------------------


def test_decision_from_another_order_cannot_authorize_this_one():
    """拿另一个案子的 eligible 判定来执行本案，属于证据不匹配，必须失败"""
    db = SessionLocal()
    try:
        run = _create_run(db, "mismatch-001", HIGH_VALUE_TICKET_KEY)
        # 判定针对低风险订单，请求却指向高金额订单
        decision = _eligible_decision(db, LOW_RISK_ORDER_KEY)

        result = create_replacement(
            db, run, _request(HIGH_VALUE_ORDER_KEY), decision
        )

        assert result.status is CreateReplacementStatus.EVIDENCE_MISMATCH
        assert _persisted_replacements(db, HIGH_VALUE_ORDER_KEY) == []
        assert _persisted_replacements(db, LOW_RISK_ORDER_KEY) == []
    finally:
        db.close()


def test_missing_order_fails_structurally():
    """判定所指订单在持久化状态中不存在时，结构化失败而不是凭空创建"""
    db = SessionLocal()
    try:
        run = _create_run(db, "missing-order-001")
        decision = _eligible_decision(db)
        # 把判定证据与请求同时指向一个不存在的订单：证据自洽，但持久化状态没有它
        stale = decision.model_copy(deep=True)
        stale.evidence.order.order_key = "order-does-not-exist"

        result = create_replacement(
            db, run, _request("order-does-not-exist"), stale
        )

        assert result.status is CreateReplacementStatus.ORDER_NOT_FOUND
        assert result.replacement is None
        assert result.failure_reason is not None
    finally:
        db.close()


def test_order_state_drift_between_decision_and_execution_blocks_execution():
    """判定之后订单被取消时，执行时刻的真实状态说了算，换货不得发生"""
    db = SessionLocal()
    try:
        run = _create_run(db, "drift-order-001")
        decision = _eligible_decision(db)

        order = db.query(Order).filter(Order.business_key == LOW_RISK_ORDER_KEY).one()
        order.status = OrderStatus.CANCELLED
        db.commit()

        result = create_replacement(db, run, _request(), decision)

        assert result.status is CreateReplacementStatus.ORDER_NOT_REPLACEABLE
        assert _persisted_replacements(db, LOW_RISK_ORDER_KEY) == []
    finally:
        db.close()


def test_inventory_drift_between_decision_and_execution_blocks_execution():
    """判定之后库存被清零时，执行时刻的真实库存说了算，换货不得发生"""
    db = SessionLocal()
    try:
        run = _create_run(db, "drift-inventory-001")
        decision = _eligible_decision(db)

        item = (
            db.query(InventoryItem)
            .filter(InventoryItem.product_sku == AVAILABLE_SKU)
            .one()
        )
        item.available_quantity = 0
        db.commit()

        result = create_replacement(db, run, _request(), decision)

        assert result.status is CreateReplacementStatus.INVENTORY_UNAVAILABLE
        assert _persisted_replacements(db, LOW_RISK_ORDER_KEY) == []
    finally:
        db.close()


def test_run_from_another_ticket_cannot_execute_this_order():
    """Run 必须与订单处在同一业务上下文，跨工单执行属于关联无效"""
    db = SessionLocal()
    try:
        # Run 挂在高金额订单的工单上，却试图为低风险订单换货
        run = _create_run(db, "linkage-invalid-001", HIGH_VALUE_TICKET_KEY)
        decision = _eligible_decision(db, LOW_RISK_ORDER_KEY)

        result = create_replacement(db, run, _request(LOW_RISK_ORDER_KEY), decision)

        assert result.status is CreateReplacementStatus.RUN_LINKAGE_INVALID
        assert _persisted_replacements(db, LOW_RISK_ORDER_KEY) == []
    finally:
        db.close()


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {},                                                     # 缺必要字段
        {"order_key": LOW_RISK_ORDER_KEY},                      # 缺 SKU 与原因
        {"order_key": "", "product_sku": AVAILABLE_SKU, "reason": REASON},
        {"order_key": LOW_RISK_ORDER_KEY, "product_sku": "", "reason": REASON},
        {"order_key": LOW_RISK_ORDER_KEY, "product_sku": AVAILABLE_SKU, "reason": ""},
        # 纯空白原因不得冒充有效原因
        {"order_key": LOW_RISK_ORDER_KEY, "product_sku": AVAILABLE_SKU, "reason": "   "},
        # 试图夹带 SQL / 通配符
        {"order_key": "order-1; DROP TABLE orders", "product_sku": AVAILABLE_SKU, "reason": REASON},
        {"order_key": "%", "product_sku": AVAILABLE_SKU, "reason": REASON},
        # 超出列宽
        {"order_key": "o" * 65, "product_sku": AVAILABLE_SKU, "reason": REASON},
        {"order_key": LOW_RISK_ORDER_KEY, "product_sku": "S" * 65, "reason": REASON},
        # 类型非法
        {"order_key": None, "product_sku": AVAILABLE_SKU, "reason": REASON},
        {"order_key": 1, "product_sku": AVAILABLE_SKU, "reason": REASON},
    ],
)
def test_invalid_request_fails_schema_validation(invalid_payload):
    """非法输入在 schema 层直接失败，不会进入应用服务后再模糊失败"""
    with pytest.raises(ValidationError):
        CreateReplacementRequest(**invalid_payload)


def test_request_rejects_arbitrary_extra_fields():
    """请求契约不接受额外字段，调用方无法夹带 force / status 之类的旁路"""
    with pytest.raises(ValidationError):
        CreateReplacementRequest(
            order_key=LOW_RISK_ORDER_KEY,
            product_sku=AVAILABLE_SKU,
            reason=REASON,
            status="created",
        )


# --------------------------------------------------------------------------
# 契约层保证：伪成功在应用内部也构造不出来
# --------------------------------------------------------------------------


def test_created_result_cannot_be_constructed_without_a_replacement():
    """没有换货单却宣称 created 的伪成功结果无法构造"""
    with pytest.raises(ValidationError):
        CreateReplacementResult(status=CreateReplacementStatus.CREATED)


def test_failure_result_cannot_carry_a_replacement():
    """失败结果不得携带换货单，避免失败被伪装成半成功"""
    record = ReplacementRecord(
        replacement_key="replacement-order-demo-001",
        status=ReplacementStatus.CREATED,
        order_key=LOW_RISK_ORDER_KEY,
        ticket_key=LOW_RISK_TICKET_KEY,
        agent_run_key="agentrun-demo",
        product_sku=AVAILABLE_SKU,
        reason=REASON,
        is_demo_data=True,
        created_at="2026-08-25T00:00:00",
    )
    with pytest.raises(ValidationError):
        CreateReplacementResult(
            status=CreateReplacementStatus.NOT_ELIGIBLE,
            replacement=record,
            failure_reason="判定不是 eligible",
        )


def test_failure_result_must_carry_a_reason():
    """失败结果必须携带结构化失败原因"""
    with pytest.raises(ValidationError):
        CreateReplacementResult(status=CreateReplacementStatus.ORDER_NOT_FOUND)


def test_duplicate_result_must_reference_the_existing_replacement():
    """重复结果必须指出已存在的换货单标识"""
    with pytest.raises(ValidationError):
        CreateReplacementResult(
            status=CreateReplacementStatus.DUPLICATE,
            failure_reason="已存在换货单",
        )


def test_non_duplicate_result_cannot_reference_an_existing_replacement():
    """非重复结果不得携带已存在换货单标识，避免语义混淆"""
    with pytest.raises(ValidationError):
        CreateReplacementResult(
            status=CreateReplacementStatus.INVENTORY_UNAVAILABLE,
            existing_replacement_key="replacement-order-demo-001",
            failure_reason="无货",
        )
