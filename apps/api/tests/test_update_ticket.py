"""T017 update_ticket Tool 的确定性测试。

覆盖任务要求的验收点：

- 成功回写真的改变了数据库中的工单状态，并落库解决结果、摘要与解决时刻；
- 回写引用的是一张**真实存在**的换货单，且必须是本工单、本次 Run 的成果；
- 换货单不存在、或属于别的工单 / 别的 Run 时，回写结构化失败，工单保持原状；
- update_ticket 失败时 Agent Run 一律不得进入 completed；
- 只有回写成功之后 Run 才被置为 completed。

前置的换货单全部经由 T011~T016 的真实实现从 seeded 数据创建，而不是手工插入。
"""
import json

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

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
    ReplacementOrder,
    Ticket,
    TicketResolution,
    TicketStatus,
)
from order_service import get_order
from orders import GetOrderRequest
from policy_service import lookup_replacement_policy
from replacement_service import create_replacement
from replacements import CreateReplacementRequest, CreateReplacementStatus
from seed_data import seed_demo_data
from ticket_service import UPDATE_TICKET_STEP_NAME, update_ticket
from tickets import (
    TicketRecord,
    UpdateTicketRequest,
    UpdateTicketResult,
    UpdateTicketStatus,
)

# 低风险场景：5 天前已送达、金额 299、商品有货，对应 ticket-demo-001
LOW_RISK_ORDER_KEY = "order-demo-001"
LOW_RISK_TICKET_KEY = "ticket-demo-001"
# 高金额场景：同样 eligible，对应 ticket-demo-002
HIGH_VALUE_ORDER_KEY = "order-demo-002"
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"

AVAILABLE_SKU = "SKU-EARBUD-PRO-01"

REPLACEMENT_REASON = "右耳耳机无声，疑似质量问题，符合换货政策"
SUMMARY = "已确认为质量问题，换货单已创建，将于 3 个工作日内发出替换商品。"

TEST_RUN_KEY_PREFIX = "agentrun-updateticket-"


def _clear_test_runs() -> None:
    """删除本模块创建的 AgentRun。

    agent_steps 与 replacement_orders 由数据库级 ON DELETE CASCADE 一并清除，
    工单上的 replacement_id 引用则由 ON DELETE SET NULL 置空。
    """
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
    """每个测试前后重新播种 demo 数据并清理本模块残留 Run。"""
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
# 夹具构造：换货单全部经由 T011~T016 的真实实现产生
# --------------------------------------------------------------------------


def _decision(db, order_key: str, sku: str):
    raw = json.dumps(
        {
            "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT.value,
            "issue_summary": "右耳耳机无声，疑似质量问题",
            "requested_action": RequestedAction.REPLACEMENT.value,
            "confidence": 0.95,
        }
    )
    intent = extract_intent(raw)
    assert intent.status is IntentExtractionStatus.SUCCESS
    decision = decide_replacement(
        intent,
        lookup_replacement_policy(db, intent.intent),
        get_order(db, GetOrderRequest(order_key=order_key)),
        check_inventory(db, CheckInventoryRequest(product_sku=sku)),
    )
    assert decision.status is ReplacementDecisionStatus.ELIGIBLE
    return decision


def _create_run(db, key: str, ticket_key: str = LOW_RISK_TICKET_KEY) -> AgentRun:
    """基于 seeded demo ticket 创建一个已持久化的 AgentRun。"""
    ticket_id = db.query(Ticket).filter(Ticket.business_key == ticket_key).one().id
    run = AgentRun(business_key=f"{TEST_RUN_KEY_PREFIX}{key}", ticket_id=ticket_id)
    db.add(run)
    db.commit()
    return run


def _real_replacement(
    db, run: AgentRun, order_key: str = LOW_RISK_ORDER_KEY
) -> str:
    """用 T016 的真实实现创建一张换货单，返回它的业务标识。"""
    result = create_replacement(
        db,
        run,
        CreateReplacementRequest(
            order_key=order_key, product_sku=AVAILABLE_SKU, reason=REPLACEMENT_REASON
        ),
        _decision(db, order_key, AVAILABLE_SKU),
    )
    assert result.status is CreateReplacementStatus.CREATED
    return result.replacement.replacement_key


def _request(
    ticket_key: str = LOW_RISK_TICKET_KEY,
    replacement_key: str = "replacement-order-demo-001",
    summary: str = SUMMARY,
) -> UpdateTicketRequest:
    return UpdateTicketRequest(
        ticket_key=ticket_key,
        resolution=TicketResolution.REPLACEMENT_CREATED,
        replacement_key=replacement_key,
        summary=summary,
    )


def _ticket(db, ticket_key: str = LOW_RISK_TICKET_KEY) -> Ticket:
    return db.query(Ticket).filter(Ticket.business_key == ticket_key).one()


def _update_step(db, run_id: int) -> AgentStep:
    return (
        db.query(AgentStep)
        .filter(
            AgentStep.agent_run_id == run_id,
            AgentStep.name == UPDATE_TICKET_STEP_NAME,
        )
        .one()
    )


# --------------------------------------------------------------------------
# 成功：工单状态被真实持久化
# --------------------------------------------------------------------------


def test_successful_write_back_persists_ticket_state():
    """成功回写真的改变了数据库中的工单状态与解决结果"""
    db = SessionLocal()
    try:
        run = _create_run(db, "success-001")
        replacement_key = _real_replacement(db, run)

        assert _ticket(db).status is TicketStatus.OPEN

        result = update_ticket(db, run, _request(replacement_key=replacement_key))

        assert result.status is UpdateTicketStatus.UPDATED
        assert result.failure_reason is None

        # 成功的唯一依据是持久化状态，而不是返回值本身
        ticket = _ticket(db)
        assert ticket.status is TicketStatus.RESOLVED
        assert ticket.resolution is TicketResolution.REPLACEMENT_CREATED
        assert ticket.resolution_summary == SUMMARY
        assert ticket.resolved_at is not None

        # 返回的类型化视图与持久化行一致
        assert result.ticket.ticket_key == LOW_RISK_TICKET_KEY
        assert result.ticket.status is TicketStatus.RESOLVED
        assert result.ticket.replacement_key == replacement_key
    finally:
        db.close()


def test_write_back_references_the_real_replacement():
    """工单上的结果引用指向那张真实存在的换货单行，而不是一个文本标识"""
    db = SessionLocal()
    try:
        run = _create_run(db, "reference-001")
        replacement_key = _real_replacement(db, run)

        result = update_ticket(db, run, _request(replacement_key=replacement_key))
        assert result.status is UpdateTicketStatus.UPDATED

        replacement = (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.business_key == replacement_key)
            .one()
        )
        ticket = _ticket(db)

        # 外键指向真实换货单行，ORM 关系同样可导航
        assert ticket.replacement_id == replacement.id
        assert ticket.resolution_replacement.business_key == replacement_key
        # 该换货单确实是本工单、本次 Run 的成果
        assert replacement.ticket_id == ticket.id
        assert replacement.agent_run_id == run.id
    finally:
        db.close()


def test_ticket_state_survives_a_new_session():
    """回写结果是独立于请求生命周期的持久化状态，换一个 session 仍然查得到"""
    db = SessionLocal()
    try:
        run = _create_run(db, "durable-001")
        replacement_key = _real_replacement(db, run)
        result = update_ticket(db, run, _request(replacement_key=replacement_key))
        assert result.status is UpdateTicketStatus.UPDATED
    finally:
        db.close()

    db = SessionLocal()
    try:
        ticket = _ticket(db)
        assert ticket.status is TicketStatus.RESOLVED
        assert ticket.resolution is TicketResolution.REPLACEMENT_CREATED
        assert ticket.resolution_replacement.business_key == replacement_key
    finally:
        db.close()


def test_successful_write_back_completes_the_agent_run():
    """只有工单真的被回写之后，Run 才被置为 completed"""
    db = SessionLocal()
    try:
        run = _create_run(db, "run-complete-001")
        replacement_key = _real_replacement(db, run)

        # 换货已经执行，但工单尚未回写：此时 Run 仍不是 completed
        assert run.status is AgentRunStatus.RUNNING
        assert run.completed_at is None

        result = update_ticket(db, run, _request(replacement_key=replacement_key))
        assert result.status is UpdateTicketStatus.UPDATED

        assert run.status is AgentRunStatus.COMPLETED
        assert run.completed_at is not None
        assert run.error_message is None
    finally:
        db.close()


def test_write_back_is_recorded_as_a_completed_run_step():
    """回写作为一个真实步骤记录在 Run 时间线上"""
    db = SessionLocal()
    try:
        run = _create_run(db, "run-step-001")
        replacement_key = _real_replacement(db, run)

        update_ticket(db, run, _request(replacement_key=replacement_key))

        step = _update_step(db, run.id)
        assert step.status is AgentStepStatus.COMPLETED
        assert step.error_message is None
        assert step.completed_at is not None
    finally:
        db.close()


def test_replaying_write_back_keeps_a_single_result_reference():
    """同一次 Run 重复回写是幂等的，工单仍然只引用那一张换货单"""
    db = SessionLocal()
    try:
        run = _create_run(db, "replay-001")
        replacement_key = _real_replacement(db, run)

        first = update_ticket(db, run, _request(replacement_key=replacement_key))
        assert first.status is UpdateTicketStatus.UPDATED

        second = update_ticket(db, run, _request(replacement_key=replacement_key))
        assert second.status is UpdateTicketStatus.UPDATED

        ticket = _ticket(db)
        assert ticket.status is TicketStatus.RESOLVED
        assert ticket.resolution_replacement.business_key == replacement_key
    finally:
        db.close()


def test_demo_data_can_be_reseeded_after_write_back():
    """回写在工单与换货单之间建立了互相引用，demo 数据仍然可重复重建"""
    db = SessionLocal()
    try:
        run = _create_run(db, "reseed-001")
        replacement_key = _real_replacement(db, run)
        assert (
            update_ticket(db, run, _request(replacement_key=replacement_key)).status
            is UpdateTicketStatus.UPDATED
        )

        seed_demo_data(db)

        ticket = _ticket(db)
        assert ticket.status is TicketStatus.OPEN
        assert ticket.replacement_id is None
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.business_key == replacement_key)
            .one_or_none()
            is None
        )
    finally:
        db.close()


# --------------------------------------------------------------------------
# 失败：结果必须有实据，且 Run 不得 completed
# --------------------------------------------------------------------------


def test_unknown_replacement_cannot_close_the_ticket():
    """引用一张不存在的换货单无法结案：工单保持原状，Run 不得 completed"""
    db = SessionLocal()
    try:
        run = _create_run(db, "no-replacement-001")

        result = update_ticket(
            db, run, _request(replacement_key="replacement-does-not-exist")
        )

        assert result.status is UpdateTicketStatus.REPLACEMENT_NOT_FOUND
        assert result.ticket is None
        assert result.failure_reason is not None

        ticket = _ticket(db)
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
        assert ticket.resolution_summary is None
        assert ticket.replacement_id is None
        assert ticket.resolved_at is None

        assert run.status is not AgentRunStatus.COMPLETED
        assert run.completed_at is None
    finally:
        db.close()


def test_replacement_from_another_ticket_cannot_close_this_ticket():
    """拿别的工单的换货单来结本工单，属于关联无效"""
    db = SessionLocal()
    try:
        # 换货单真实存在，但它属于高金额工单
        other_run = _create_run(db, "cross-ticket-001", HIGH_VALUE_TICKET_KEY)
        replacement_key = _real_replacement(db, other_run, HIGH_VALUE_ORDER_KEY)

        run = _create_run(db, "cross-ticket-002", LOW_RISK_TICKET_KEY)
        result = update_ticket(db, run, _request(replacement_key=replacement_key))

        assert result.status is UpdateTicketStatus.REPLACEMENT_LINKAGE_INVALID
        assert result.ticket is None

        ticket = _ticket(db, LOW_RISK_TICKET_KEY)
        assert ticket.status is TicketStatus.OPEN
        assert ticket.replacement_id is None
        assert run.status is not AgentRunStatus.COMPLETED
    finally:
        db.close()


def test_replacement_from_another_run_cannot_be_claimed():
    """换货单属于本工单，但不是本次 Run 执行的成果，同样不得据此结案"""
    db = SessionLocal()
    try:
        executing_run = _create_run(db, "cross-run-001")
        replacement_key = _real_replacement(db, executing_run)

        # 另一次 Run 挂在同一张工单上，试图认领别人的执行成果
        other_run = _create_run(db, "cross-run-002")
        result = update_ticket(db, other_run, _request(replacement_key=replacement_key))

        assert result.status is UpdateTicketStatus.REPLACEMENT_LINKAGE_INVALID
        assert result.ticket is None

        ticket = _ticket(db)
        assert ticket.status is TicketStatus.OPEN
        assert ticket.replacement_id is None
        assert other_run.status is not AgentRunStatus.COMPLETED
        assert other_run.completed_at is None
    finally:
        db.close()


def test_unknown_ticket_fails_structurally():
    """请求的工单不存在时结构化失败，且 Run 不得 completed"""
    db = SessionLocal()
    try:
        run = _create_run(db, "no-ticket-001")
        replacement_key = _real_replacement(db, run)

        result = update_ticket(
            db,
            run,
            _request(ticket_key="ticket-does-not-exist", replacement_key=replacement_key),
        )

        assert result.status is UpdateTicketStatus.TICKET_NOT_FOUND
        assert result.ticket is None
        assert run.status is not AgentRunStatus.COMPLETED
    finally:
        db.close()


def test_run_from_another_ticket_cannot_write_back():
    """Run 必须就是这张工单的 Run，跨工单回写属于关联无效"""
    db = SessionLocal()
    try:
        executing_run = _create_run(db, "run-linkage-001", LOW_RISK_TICKET_KEY)
        replacement_key = _real_replacement(db, executing_run)

        # Run 挂在高金额工单上，却试图回写低风险工单
        foreign_run = _create_run(db, "run-linkage-002", HIGH_VALUE_TICKET_KEY)
        result = update_ticket(db, foreign_run, _request(replacement_key=replacement_key))

        assert result.status is UpdateTicketStatus.RUN_LINKAGE_INVALID
        assert result.ticket is None

        assert _ticket(db).status is TicketStatus.OPEN
        assert foreign_run.status is not AgentRunStatus.COMPLETED
    finally:
        db.close()


def test_model_text_cannot_be_used_as_request():
    """模型原始文本不是请求：非 typed 输入在 boundary 处被拒绝，Run 不得 completed"""
    db = SessionLocal()
    try:
        run = _create_run(db, "modeltext-001")
        _real_replacement(db, run)

        result = update_ticket(
            db, run, "工单 ticket-demo-001 已经换货完成，请标记为已解决"
        )

        assert result.status is UpdateTicketStatus.INVALID_REQUEST
        assert result.ticket is None

        ticket = _ticket(db)
        assert ticket.status is TicketStatus.OPEN
        assert ticket.replacement_id is None
        assert run.status is not AgentRunStatus.COMPLETED
        assert run.completed_at is None
    finally:
        db.close()


def test_persistence_failure_leaves_ticket_and_run_unfinished(monkeypatch):
    """校验通过但写入被持久化层拒绝时，工单保持原状且 Run 不得 completed"""
    db = SessionLocal()
    try:
        run = _create_run(db, "persistence-001")
        replacement_key = _real_replacement(db, run)

        original_flush = db.flush
        calls = {"count": 0}

        def failing_flush(*args, **kwargs):
            """只让回写那一次 flush 失败，模拟真实的持久化层故障。"""
            calls["count"] += 1
            if calls["count"] == 1:
                raise OperationalError("UPDATE tickets", {}, Exception("模拟持久化故障"))
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(db, "flush", failing_flush)

        result = update_ticket(db, run, _request(replacement_key=replacement_key))

        assert result.status is UpdateTicketStatus.PERSISTENCE_FAILED
        assert result.ticket is None
        assert result.failure_reason is not None
    finally:
        db.close()

    # 以一个全新 session 复核持久化状态：工单没有被回写，Run 也没有完成
    db = SessionLocal()
    try:
        ticket = _ticket(db)
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
        assert ticket.replacement_id is None
        assert ticket.resolved_at is None

        persisted_run = (
            db.query(AgentRun)
            .filter(AgentRun.business_key == f"{TEST_RUN_KEY_PREFIX}persistence-001")
            .one()
        )
        assert persisted_run.status is not AgentRunStatus.COMPLETED
        assert persisted_run.completed_at is None
    finally:
        db.close()


def test_write_back_rejected_by_real_foreign_key_leaves_run_unfinished(monkeypatch):
    """换货单在校验之后、写入之前消失时，真实的外键约束拒绝回写，Run 不得 completed

    与上一个用例不同，这里没有模拟任何异常：换货单由另一个 session 真的删除，
    UPDATE 被 PostgreSQL 的外键约束拒绝。这正是"不得把无法验证存在的 replacement
    写回工单"在数据库层的最后一道防线。
    """
    db = SessionLocal()
    try:
        run = _create_run(db, "fk-conflict-001")
        replacement_key = _real_replacement(db, run)

        original_flush = db.flush
        state = {"deleted": False}

        def flush_after_replacement_disappears(*args, **kwargs):
            # 只在回写那一次 flush 之前制造状态漂移：另一个连接删除换货单并提交
            if not state["deleted"]:
                state["deleted"] = True
                other = SessionLocal()
                try:
                    other.query(ReplacementOrder).filter(
                        ReplacementOrder.business_key == replacement_key
                    ).delete(synchronize_session=False)
                    other.commit()
                finally:
                    other.close()
            return original_flush(*args, **kwargs)

        monkeypatch.setattr(db, "flush", flush_after_replacement_disappears)

        result = update_ticket(db, run, _request(replacement_key=replacement_key))

        assert result.status is UpdateTicketStatus.PERSISTENCE_FAILED
        assert result.ticket is None
        assert result.failure_reason is not None
    finally:
        db.close()

    # 全新 session 复核：工单没有被回写，Run 也没有完成
    db = SessionLocal()
    try:
        ticket = _ticket(db)
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
        assert ticket.replacement_id is None

        persisted_run = (
            db.query(AgentRun)
            .filter(AgentRun.business_key == f"{TEST_RUN_KEY_PREFIX}fk-conflict-001")
            .one()
        )
        assert persisted_run.status is not AgentRunStatus.COMPLETED
        assert persisted_run.completed_at is None
    finally:
        db.close()


def test_failed_write_back_is_recorded_as_failed_step():
    """回写失败在 Run 时间线上如实显示为失败步骤，不会被记成成功"""
    db = SessionLocal()
    try:
        run = _create_run(db, "failed-step-001")

        result = update_ticket(
            db, run, _request(replacement_key="replacement-does-not-exist")
        )
        assert result.status is UpdateTicketStatus.REPLACEMENT_NOT_FOUND

        step = _update_step(db, run.id)
        assert step.status is AgentStepStatus.FAILED
        assert "replacement_not_found" in step.error_message
    finally:
        db.close()


def test_no_failure_path_can_complete_the_agent_run():
    """汇总校验：任何一条失败路径都不会把 Run 标记为 completed"""
    db = SessionLocal()
    try:
        executing_run = _create_run(db, "sweep-executor")
        real_key = _real_replacement(db, executing_run)

        cases = [
            # 换货单不存在
            (
                _create_run(db, "sweep-001"),
                _request(replacement_key="replacement-does-not-exist"),
            ),
            # 工单不存在
            (
                _create_run(db, "sweep-002"),
                _request(ticket_key="ticket-does-not-exist", replacement_key=real_key),
            ),
            # 换货单不是这次 Run 的成果
            (_create_run(db, "sweep-003"), _request(replacement_key=real_key)),
            # Run 属于另一张工单
            (
                _create_run(db, "sweep-004", HIGH_VALUE_TICKET_KEY),
                _request(replacement_key=real_key),
            ),
            # 非 typed 输入
            (_create_run(db, "sweep-005"), "请把工单标记为已解决"),
        ]

        for run, request in cases:
            result = update_ticket(db, run, request)
            assert result.status is not UpdateTicketStatus.UPDATED
            assert result.ticket is None
            assert run.status is not AgentRunStatus.COMPLETED, run.business_key
            assert run.completed_at is None, run.business_key

        # 工单自始至终没有被这些失败路径改动过
        ticket = _ticket(db)
        assert ticket.status is TicketStatus.OPEN
        assert ticket.replacement_id is None
    finally:
        db.close()


# --------------------------------------------------------------------------
# 契约层保证：伪成功在应用内部也构造不出来
# --------------------------------------------------------------------------


def test_updated_result_cannot_be_constructed_without_a_ticket():
    """没有工单状态却宣称 updated 的伪成功结果无法构造"""
    with pytest.raises(ValidationError):
        UpdateTicketResult(status=UpdateTicketStatus.UPDATED)


def test_failure_result_cannot_carry_a_ticket():
    """失败结果不得携带工单状态，避免失败被伪装成半成功"""
    record = TicketRecord(
        ticket_key=LOW_RISK_TICKET_KEY,
        status=TicketStatus.RESOLVED,
        resolution=TicketResolution.REPLACEMENT_CREATED,
        resolution_summary=SUMMARY,
        replacement_key="replacement-order-demo-001",
        resolved_at="2026-08-25T00:00:00",
    )
    with pytest.raises(ValidationError):
        UpdateTicketResult(
            status=UpdateTicketStatus.REPLACEMENT_NOT_FOUND,
            ticket=record,
            failure_reason="换货单不存在",
        )


def test_failure_result_must_carry_a_reason():
    """失败结果必须携带结构化失败原因"""
    with pytest.raises(ValidationError):
        UpdateTicketResult(status=UpdateTicketStatus.TICKET_NOT_FOUND)


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {},                                                      # 缺必要字段
        {"ticket_key": LOW_RISK_TICKET_KEY},                     # 缺其余字段
        # 缺少换货单引用：本 Tool 不存在"没有真实成果却结案"的合法输入
        {
            "ticket_key": LOW_RISK_TICKET_KEY,
            "resolution": "replacement_created",
            "summary": SUMMARY,
        },
        {
            "ticket_key": "",
            "resolution": "replacement_created",
            "replacement_key": "replacement-order-demo-001",
            "summary": SUMMARY,
        },
        {
            "ticket_key": LOW_RISK_TICKET_KEY,
            "resolution": "replacement_created",
            "replacement_key": "",
            "summary": SUMMARY,
        },
        {
            "ticket_key": LOW_RISK_TICKET_KEY,
            "resolution": "replacement_created",
            "replacement_key": "replacement-order-demo-001",
            "summary": "",
        },
        # 纯空白摘要不得冒充结案说明
        {
            "ticket_key": LOW_RISK_TICKET_KEY,
            "resolution": "replacement_created",
            "replacement_key": "replacement-order-demo-001",
            "summary": "   ",
        },
        # 不存在的解决结果不得被凭空发明
        {
            "ticket_key": LOW_RISK_TICKET_KEY,
            "resolution": "refunded",
            "replacement_key": "replacement-order-demo-001",
            "summary": SUMMARY,
        },
        # 试图夹带 SQL / 通配符
        {
            "ticket_key": "ticket-1; DROP TABLE tickets",
            "resolution": "replacement_created",
            "replacement_key": "replacement-order-demo-001",
            "summary": SUMMARY,
        },
        {
            "ticket_key": "%",
            "resolution": "replacement_created",
            "replacement_key": "replacement-order-demo-001",
            "summary": SUMMARY,
        },
        # 超出列宽
        {
            "ticket_key": "t" * 65,
            "resolution": "replacement_created",
            "replacement_key": "replacement-order-demo-001",
            "summary": SUMMARY,
        },
        {
            "ticket_key": LOW_RISK_TICKET_KEY,
            "resolution": "replacement_created",
            "replacement_key": "r" * 81,
            "summary": SUMMARY,
        },
        # 类型非法
        {
            "ticket_key": None,
            "resolution": "replacement_created",
            "replacement_key": "replacement-order-demo-001",
            "summary": SUMMARY,
        },
        {
            "ticket_key": 1,
            "resolution": "replacement_created",
            "replacement_key": "replacement-order-demo-001",
            "summary": SUMMARY,
        },
    ],
)
def test_invalid_request_fails_schema_validation(invalid_payload):
    """非法输入在 schema 层直接失败，不会进入应用服务后再模糊失败"""
    with pytest.raises(ValidationError):
        UpdateTicketRequest(**invalid_payload)


def test_request_rejects_arbitrary_extra_fields():
    """请求契约不接受额外字段，调用方无法直接指定工单终态"""
    with pytest.raises(ValidationError):
        UpdateTicketRequest(
            ticket_key=LOW_RISK_TICKET_KEY,
            resolution=TicketResolution.REPLACEMENT_CREATED,
            replacement_key="replacement-order-demo-001",
            summary=SUMMARY,
            status="resolved",
        )
