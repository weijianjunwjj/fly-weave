"""T018 端到端 Golden Path 编排的确定性测试。

覆盖任务要求的验收点：

- 一次操作即可驱动完整流程：从种子工单进入系统，一直执行到换货成功并回写工单；
- Agent Run timeline 反映真实执行顺序与真实状态，步骤全部来自实际调用结果；
- replacement 真实落库，可在全新 session 中读回，并被工单以外键真实引用；
- 任一 Tool 失败都不会被误报为成功：Run 保持 failed，不产生换货单，工单不被回写，
  失败之后的步骤根本不会出现在时间线上。

全部前置数据来自 ``seed_demo_data`` 的种子工单，流程全程走 T011~T017 的真实实现，
测试不手工插入换货单，也不伪造任何步骤状态。
"""
import pytest
from fastapi.testclient import TestClient

from agent_run_service import (
    DECISION_STEP_NAME,
    GOLDEN_PATH_STEP_ORDER,
    ORDER_STEP_NAME,
    run_golden_path,
)
from database import SessionLocal
from intent_service import INTENT_STEP_NAME
from inventory_service import INVENTORY_CHECK_STEP_NAME
from main import app
from models import (
    AgentRun,
    AgentRunStatus,
    AgentStepStatus,
    InventoryItem,
    Order,
    ReplacementOrder,
    Ticket,
    TicketResolution,
    TicketStatus,
)
from replacement_service import REPLACEMENT_STEP_NAME
from seed_data import seed_demo_data
from ticket_service import UPDATE_TICKET_STEP_NAME

client = TestClient(app)

# 低风险场景：5 天前已送达、金额 299、商品有货 —— 唯一能走完整条成功路径的种子工单
LOW_RISK_TICKET_KEY = "ticket-demo-001"
LOW_RISK_ORDER_KEY = "order-demo-001"
AVAILABLE_SKU = "SKU-EARBUD-PRO-01"

# 高金额场景：业务条件 eligible，但金额超过政策人工审批阈值
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"
HIGH_VALUE_ORDER_KEY = "order-demo-002"

# 拒绝场景：购买已 60 天且商品无货，判定必然 blocked
REJECTED_TICKET_KEY = "ticket-demo-003"
REJECTED_ORDER_KEY = "order-demo-003"

EXPECTED_STEP_NAMES = [name for _, name in GOLDEN_PATH_STEP_ORDER]
EXPECTED_STEP_ORDERS = [order for order, _ in GOLDEN_PATH_STEP_ORDER]


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据。

    ``seed_demo_data`` 会先清空 demo 工单，数据库级 ON DELETE CASCADE 随之清除本
    模块产生的 AgentRun、步骤与换货单，因此测试可反复运行且互不干扰。
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



def _run(ticket_key: str) -> str:
    """对给定种子工单执行一次真实的完整流程，返回本次 Run 的业务标识。"""
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.business_key == ticket_key).one()
        agent_run = run_golden_path(db, ticket)
        return agent_run.business_key
    finally:
        db.close()


def _persisted_run_status(run_key: str) -> AgentRunStatus:
    """在一个全新 session 中读回该 Run 的持久化状态。"""
    db = SessionLocal()
    try:
        return (
            db.query(AgentRun).filter(AgentRun.business_key == run_key).one().status
        )
    finally:
        db.close()


# --------------------------------------------------------------------------
# 完整成功路径
# --------------------------------------------------------------------------


def test_golden_path_runs_seeded_ticket_through_to_a_completed_run():
    """一次操作驱动完整流程，Run 以真实的 completed 终态结束"""
    run_key = _run(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = db.query(AgentRun).filter(
            AgentRun.business_key == run_key
        ).one()

        assert agent_run.status is AgentRunStatus.COMPLETED
        assert agent_run.started_at is not None
        assert agent_run.completed_at is not None
        assert agent_run.error_message is None
        assert agent_run.ticket.business_key == LOW_RISK_TICKET_KEY
    finally:
        db.close()


def test_timeline_records_every_real_step_in_execution_order():
    """时间线按真实执行顺序记录七个步骤，且状态全部来自真实调用结果"""
    run_key = _run(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = db.query(AgentRun).filter(
            AgentRun.business_key == run_key
        ).one()

        assert [step.step_order for step in agent_run.steps] == EXPECTED_STEP_ORDERS
        assert [step.name for step in agent_run.steps] == EXPECTED_STEP_NAMES
        assert all(
            step.status is AgentStepStatus.COMPLETED for step in agent_run.steps
        )
        assert all(step.completed_at is not None for step in agent_run.steps)
        assert all(step.error_message is None for step in agent_run.steps)
    finally:
        db.close()


def test_successful_run_persists_a_real_replacement():
    """换货单真实落库，并携带订单 / 工单 / Run 三条真实业务关联"""
    run_key = _run(LOW_RISK_TICKET_KEY)

    # 用全新 session 读回，证明换货单确实写进了数据库
    db = SessionLocal()
    try:
        replacement = (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.business_key == f"replacement-{LOW_RISK_ORDER_KEY}")
            .one()
        )

        assert replacement.product_sku == AVAILABLE_SKU
        assert replacement.reason
        assert replacement.order.business_key == LOW_RISK_ORDER_KEY
        assert replacement.ticket.business_key == LOW_RISK_TICKET_KEY
        assert replacement.agent_run.business_key == run_key
    finally:
        db.close()


def test_successful_run_writes_the_final_ticket_result():
    """流程末尾通过既有回写能力真实更新工单状态与结果引用"""
    _run(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        ticket = (
            db.query(Ticket).filter(Ticket.business_key == LOW_RISK_TICKET_KEY).one()
        )

        assert ticket.status is TicketStatus.RESOLVED
        assert ticket.resolution is TicketResolution.REPLACEMENT_CREATED
        assert ticket.resolved_at is not None
        # 工单引用的是数据库里那一行真实换货单，而不是一段文本
        assert ticket.resolution_replacement is not None
        assert (
            ticket.resolution_replacement.business_key
            == f"replacement-{LOW_RISK_ORDER_KEY}"
        )
        assert ticket.resolution_replacement.business_key in ticket.resolution_summary
    finally:
        db.close()


# --------------------------------------------------------------------------
# 风险门禁：高风险动作暂停等待审批
# --------------------------------------------------------------------------


def test_high_value_case_waits_for_approval_without_business_side_effects():
    """高金额 eligible 案例被风险门禁暂停，不创建换货单，也不回写工单。"""
    run_key = _run(HIGH_VALUE_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = (
            db.query(AgentRun)
            .filter(AgentRun.business_key == run_key)
            .one()
        )

        # Run 没有失败，也绝不能伪装成完成。
        assert agent_run.status is AgentRunStatus.WAITING_FOR_APPROVAL
        assert agent_run.started_at is not None
        assert agent_run.completed_at is None
        assert agent_run.error_message is None
        assert agent_run.ticket.business_key == HIGH_VALUE_TICKET_KEY

        steps = {step.name: step for step in agent_run.steps}

        # 风险门禁之前的真实步骤已经完成。
        assert REPLACEMENT_STEP_NAME in steps

        replacement_step = steps[REPLACEMENT_STEP_NAME]

        # create_replacement 被拦截：
        # 这个步骤既没有完成，也没有失败，而是保持 pending。
        assert replacement_step.status is AgentStepStatus.PENDING
        assert replacement_step.completed_at is None
        assert replacement_step.error_message is not None
        assert "approval_required" in replacement_step.error_message

        # 受保护动作没有成功，因此最终工单回写步骤绝不能启动。
        assert UPDATE_TICKET_STEP_NAME not in steps

        order = (
            db.query(Order)
            .filter(Order.business_key == HIGH_VALUE_ORDER_KEY)
            .one()
        )

        # 真正的安全边界：
        # 数据库中没有任何换货单副作用。
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.order_id == order.id)
            .one_or_none()
            is None
        )

        ticket = (
            db.query(Ticket)
            .filter(Ticket.business_key == HIGH_VALUE_TICKET_KEY)
            .one()
        )

        # 工单仍保持原始状态，没有被 update_ticket 伪造为解决。
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
        assert ticket.resolved_at is None
        assert ticket.replacement_id is None

    finally:
        db.close()

# --------------------------------------------------------------------------
# Tool 失败不得被误报为成功
# --------------------------------------------------------------------------


def test_blocked_decision_fails_the_run_without_creating_a_replacement():
    """判定被真实证据阻断时，Run 失败，且不产生换货单、不回写工单"""
    run_key = _run(REJECTED_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = db.query(AgentRun).filter(
            AgentRun.business_key == run_key
        ).one()

        assert agent_run.status is AgentRunStatus.FAILED
        assert agent_run.error_message
        assert DECISION_STEP_NAME in agent_run.error_message

        steps = {step.name: step for step in agent_run.steps}
        assert steps[DECISION_STEP_NAME].status is AgentStepStatus.FAILED
        assert steps[DECISION_STEP_NAME].error_message
        # 判定之后的步骤从未执行，因此根本不存在于时间线上
        assert REPLACEMENT_STEP_NAME not in steps
        assert UPDATE_TICKET_STEP_NAME not in steps

        order = (
            db.query(Order).filter(Order.business_key == REJECTED_ORDER_KEY).one()
        )
        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.order_id == order.id)
            .one_or_none()
            is None
        )

        ticket = (
            db.query(Ticket).filter(Ticket.business_key == REJECTED_TICKET_KEY).one()
        )
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
        assert ticket.replacement_id is None
    finally:
        db.close()


def test_inventory_tool_failure_is_not_reported_as_success():
    """库存 Tool 真实失败（查无 SKU）时，Run 失败且流程就此终止"""
    # 删除库存记录，使 check_inventory 真实返回 sku_not_found
    db = SessionLocal()
    try:
        db.query(InventoryItem).filter(
            InventoryItem.product_sku == AVAILABLE_SKU
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

    run_key = _run(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = db.query(AgentRun).filter(
            AgentRun.business_key == run_key
        ).one()

        assert agent_run.status is AgentRunStatus.FAILED
        assert "sku_not_found" in agent_run.error_message

        steps = {step.name: step for step in agent_run.steps}
        assert steps[INVENTORY_CHECK_STEP_NAME].status is AgentStepStatus.FAILED
        # 库存之前的步骤真实成功，之后的步骤从未执行
        assert steps[ORDER_STEP_NAME].status is AgentStepStatus.COMPLETED
        assert DECISION_STEP_NAME not in steps
        assert REPLACEMENT_STEP_NAME not in steps
        assert UPDATE_TICKET_STEP_NAME not in steps

        assert db.query(ReplacementOrder).count() == 0

        ticket = (
            db.query(Ticket).filter(Ticket.business_key == LOW_RISK_TICKET_KEY).one()
        )
        assert ticket.status is TicketStatus.OPEN
        assert ticket.resolution is None
    finally:
        db.close()


def test_replacement_tool_failure_keeps_the_second_run_failed():
    """换货 Tool 拒绝重复执行时，第二次 Run 失败，且不产生第二张换货单"""
    first_run_key = _run(LOW_RISK_TICKET_KEY)
    second_run_key = _run(LOW_RISK_TICKET_KEY)

    assert first_run_key != second_run_key

    db = SessionLocal()
    try:
        first_run = db.query(AgentRun).filter(
            AgentRun.business_key == first_run_key
        ).one()
        second_run = db.query(AgentRun).filter(
            AgentRun.business_key == second_run_key
        ).one()

        assert first_run.status is AgentRunStatus.COMPLETED
        assert second_run.status is AgentRunStatus.FAILED
        assert "duplicate" in second_run.error_message

        steps = {step.name: step for step in second_run.steps}
        assert steps[REPLACEMENT_STEP_NAME].status is AgentStepStatus.FAILED
        # 换货失败之后不得继续回写工单
        assert UPDATE_TICKET_STEP_NAME not in steps

        # 数据库里始终只有第一次执行创建的那一张换货单
        replacements = db.query(ReplacementOrder).all()
        assert len(replacements) == 1
        assert replacements[0].agent_run_id == first_run.id
    finally:
        db.close()


def test_intent_failure_stops_the_run_at_the_first_step():
    """没有可提案依据时 intent 步骤真实失败，流程不会继续走向成功"""
    # 清除应用自己标注的换货场景，使确定性提案来源返回 None
    db = SessionLocal()
    try:
        ticket = (
            db.query(Ticket).filter(Ticket.business_key == LOW_RISK_TICKET_KEY).one()
        )
        ticket.demo_scenario = None
        db.commit()
    finally:
        db.close()

    run_key = _run(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        agent_run = db.query(AgentRun).filter(
            AgentRun.business_key == run_key
        ).one()

        assert agent_run.status is AgentRunStatus.FAILED
        assert "model_failure" in agent_run.error_message

        assert [step.name for step in agent_run.steps] == [INTENT_STEP_NAME]
        assert agent_run.steps[0].status is AgentStepStatus.FAILED

        assert db.query(ReplacementOrder).count() == 0
    finally:
        db.close()


# --------------------------------------------------------------------------
# 单次启动入口（HTTP）
# --------------------------------------------------------------------------


def test_start_endpoint_drives_the_whole_flow_in_one_call():
    """一次 POST 真实驱动整条流程，响应中的成功由真实 Tool 结果决定"""
    response = client.post(f"/tickets/{LOW_RISK_TICKET_KEY}/agent-runs")
    assert response.status_code == 201

    payload = response.json()
    assert payload["ticket_key"] == LOW_RISK_TICKET_KEY
    assert payload["status"] == "completed"
    assert payload["error_message"] is None
    assert [step["name"] for step in payload["steps"]] == EXPECTED_STEP_NAMES
    assert all(step["status"] == "completed" for step in payload["steps"])

    assert payload["replacement"] is not None
    assert payload["replacement"]["business_key"] == f"replacement-{LOW_RISK_ORDER_KEY}"
    assert payload["replacement"]["product_sku"] == AVAILABLE_SKU

    assert payload["ticket_result"]["status"] == "resolved"
    assert payload["ticket_result"]["resolution"] == "replacement_created"
    assert (
        payload["ticket_result"]["replacement_key"]
        == f"replacement-{LOW_RISK_ORDER_KEY}"
    )

    # 响应中的状态必须与持久化状态一致
    assert _persisted_run_status(payload["business_key"]) is AgentRunStatus.COMPLETED


def test_start_endpoint_reports_tool_failure_as_a_failed_run():
    """Tool 失败时端点不得显示成功：状态为 failed，且没有换货单与工单结果"""
    response = client.post(f"/tickets/{REJECTED_TICKET_KEY}/agent-runs")
    # 请求本身完成了，但这不等于执行成功
    assert response.status_code == 201

    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["error_message"]
    assert payload["replacement"] is None
    assert payload["ticket_result"]["status"] == "open"
    assert payload["ticket_result"]["resolution"] is None
    assert payload["ticket_result"]["replacement_key"] is None

    step_names = [step["name"] for step in payload["steps"]]
    assert REPLACEMENT_STEP_NAME not in step_names
    assert UPDATE_TICKET_STEP_NAME not in step_names
    assert any(step["status"] == "failed" for step in payload["steps"])


def test_start_endpoint_returns_404_for_unknown_ticket():
    """未知工单返回诚实的 404，不创建任何 Run"""
    response = client.post("/tickets/ticket-does-not-exist/agent-runs")
    assert response.status_code == 404


def test_latest_endpoint_reports_only_real_runs():
    """尚未执行时 latest 返回 404；执行之后返回那次真实 Run"""
    not_run_yet = client.get(f"/tickets/{LOW_RISK_TICKET_KEY}/agent-runs/latest")
    assert not_run_yet.status_code == 404

    started = client.post(f"/tickets/{LOW_RISK_TICKET_KEY}/agent-runs")
    assert started.status_code == 201

    latest = client.get(f"/tickets/{LOW_RISK_TICKET_KEY}/agent-runs/latest")
    assert latest.status_code == 200
    assert latest.json()["business_key"] == started.json()["business_key"]
    assert latest.json()["status"] == "completed"

def test_start_endpoint_reports_high_risk_as_waiting_for_approval():
    """高风险 Golden Path 的 HTTP 响应必须诚实展示等待审批和结构化风险原因。"""
    response = client.post(
        f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs"
    )

    # HTTP 请求本身成功完成。
    # 201 不意味着业务动作已经执行成功。
    assert response.status_code == 201

    payload = response.json()

    assert payload["ticket_key"] == HIGH_VALUE_TICKET_KEY
    assert payload["status"] == "waiting_for_approval"
    assert payload["error_message"] is None

    # 风险门禁已经阻止换货单真正产生。
    assert payload["replacement"] is None

    # T019 必须把结构化风险原因交给 UI，
    # UI 不允许自行重新推导金额规则。
    assert payload["risk"] is not None

    risk = payload["risk"]

    assert risk["action"] == "create_replacement"
    assert risk["level"] == "high"
    assert (
        risk["rule_code"]
        == "order_amount_above_approval_threshold"
    )
    assert risk["requires_approval"] is True
    assert risk["reason"]

    assert risk["order_key"] == HIGH_VALUE_ORDER_KEY
    assert risk["order_amount"] is not None
    assert risk["approval_threshold_amount"] is not None
    assert risk["policy_key"] is not None

    # 工单没有进入最终成功结果。
    assert payload["ticket_result"]["status"] == "open"
    assert payload["ticket_result"]["resolution"] is None
    assert payload["ticket_result"]["replacement_key"] is None

    steps = {
        step["name"]: step
        for step in payload["steps"]
    }

    assert REPLACEMENT_STEP_NAME in steps

    replacement_step = steps[REPLACEMENT_STEP_NAME]
    assert replacement_step["status"] == "pending"
    assert replacement_step["completed_at"] is None
    assert replacement_step["error_message"] is not None
    assert "approval_required" in replacement_step["error_message"]

    # 风险门禁命中后不得继续执行 update_ticket。
    assert UPDATE_TICKET_STEP_NAME not in steps

    # API 返回的 waiting_for_approval
    # 必须与数据库中的真实 Run 状态完全一致。
    assert (
        _persisted_run_status(payload["business_key"])
        is AgentRunStatus.WAITING_FOR_APPROVAL
    )

    db = SessionLocal()
    try:
        order = (
            db.query(Order)
            .filter(Order.business_key == HIGH_VALUE_ORDER_KEY)
            .one()
        )

        assert (
            db.query(ReplacementOrder)
            .filter(ReplacementOrder.order_id == order.id)
            .one_or_none()
            is None
        )
    finally:
        db.close()
