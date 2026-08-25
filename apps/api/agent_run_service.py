"""Golden Path 的端到端编排（T018）。

把 T010~T017 已经各自建成的能力串成一条真实可执行的流程：

    创建 AgentRun（T010 的持久化模型）
      1 提取客户意图    intent_service.extract_and_persist_intent   （T011）
      2 检索售后政策    policy_service.lookup_replacement_policy    （T012）
      3 查询订单信息    order_service.get_order                     （T013）
      4 检查换货库存    inventory_service.check_and_persist_inventory（T014）
      5 评估换货资格    decision_service.decide_replacement         （T015）
      6 创建换货单      replacement_service.create_replacement      （T016）
      7 回写工单结果    ticket_service.update_ticket                （T017）

本模块只负责**顺序与终态**，不重新实现任何 Tool、换货写入或工单回写：每一步都
调用既有服务，并把它们真实返回的 typed 结果作为是否继续的唯一依据。

三条不可让步的性质：

1. **步骤在执行之后才被写成终态。** 步骤记录一律发生在对应服务返回之后，状态
   与失败原因都取自那次调用的真实结果，不存在"先写成功再补执行"。
2. **失败即终止。** 任何一步没有达成它的成功结果，本模块立即把 Run 置为
   ``FAILED`` 并返回，后续步骤根本不会被创建 —— 时间线上不会出现从未执行过的
   步骤，更不会出现伪造的后续成功。
3. **成功不由本模块宣布。** ``AgentRun`` 进入 ``COMPLETED`` 的唯一入口仍然是
   ``ticket_service``，且只在工单真的被回写之后。本模块只会把 Run 置为 FAILED。

不在本任务范围内：审批闸门、人工确认、风险分级（T019 / T020）。
``ReplacementDecision.policy_approval_threshold_exceeded`` 在这里被原样携带但
不参与任何控制流，本模块不因它暂停、分流或拒绝执行。
"""
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from decision_service import decide_replacement
from decisions import ReplacementDecisionStatus
from intent_proposal import propose_replacement_intent
from intent_service import INTENT_STEP_NAME, extract_and_persist_intent
from intents import IntentExtractionStatus
from inventory import CheckInventoryRequest, InventoryCheckStatus
from inventory_service import (
    INVENTORY_CHECK_STEP_NAME,
    check_and_persist_inventory,
)
from models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    Ticket,
    TicketResolution,
)
from order_service import get_order
from orders import GetOrderRequest, OrderLookupStatus
from policies import PolicyLookupStatus
from policy_service import lookup_replacement_policy
from replacement_service import REPLACEMENT_STEP_NAME, create_replacement
from replacements import CreateReplacementRequest, CreateReplacementStatus
from ticket_service import UPDATE_TICKET_STEP_NAME, update_ticket
from tickets import UpdateTicketRequest, UpdateTicketStatus

# 由本模块记录的三个步骤。序号 1 / 4 / 6 / 7 分别由 intent / inventory /
# replacement / ticket 四个既有服务自己记录，这里只补齐它们之间留空的 2 / 3 / 5，
# 使整条时间线的 step_order 连续且与真实执行顺序一致。
POLICY_STEP_NAME = "检索售后政策"
POLICY_STEP_ORDER = 2
ORDER_STEP_NAME = "查询订单信息"
ORDER_STEP_ORDER = 3
DECISION_STEP_NAME = "评估换货资格"
DECISION_STEP_ORDER = 5

# Golden Path 的完整步骤顺序，供 UI 与测试引用真实的步骤名称与序号
GOLDEN_PATH_STEP_ORDER: tuple[tuple[int, str], ...] = (
    (1, INTENT_STEP_NAME),
    (POLICY_STEP_ORDER, POLICY_STEP_NAME),
    (ORDER_STEP_ORDER, ORDER_STEP_NAME),
    (4, INVENTORY_CHECK_STEP_NAME),
    (DECISION_STEP_ORDER, DECISION_STEP_NAME),
    (6, REPLACEMENT_STEP_NAME),
    (7, UPDATE_TICKET_STEP_NAME),
)

# Run 业务标识的确定性前缀。与既有 business_key 约定一致：对外只用稳定标识符。
RUN_KEY_PREFIX = "run-"
# AgentRun.business_key 列宽 64，减去前缀与 "-000" 序号后缀后可用于工单标识的长度
_MAX_TICKET_KEY_IN_RUN_KEY = 64 - len(RUN_KEY_PREFIX) - 4
# 并发下 business_key 冲突时的重试次数
_MAX_RUN_KEY_ATTEMPTS = 5

# 回写工单的结果摘要列宽，与 UpdateTicketRequest.summary 保持一致
_MAX_SUMMARY_LENGTH = 1000


def run_golden_path(
    db: Session, ticket: Ticket, propose_intent=propose_replacement_intent
) -> AgentRun:
    """为一张已持久化工单创建并执行一次完整的 Agent Run。

    返回执行结束后的 ``AgentRun``。它的状态就是这次执行的真实终态：只有工单被
    真正回写时才是 ``COMPLETED``（由 ``ticket_service`` 置位），其余一律是
    ``FAILED`` 并带有结构化失败原因。
    """
    agent_run = _create_agent_run(db, ticket)
    try:
        return _execute(db, agent_run, ticket, propose_intent)
    except Exception as exc:  # noqa: BLE001 - 未预期异常同样不得让 Run 停在 running
        # 执行中断不是成功，也不允许 Run 悬停在 running：回滚后如实记为失败。
        db.rollback()
        reloaded = db.query(AgentRun).filter(AgentRun.id == agent_run.id).one_or_none()
        if reloaded is None:
            raise
        return _fail_run(db, reloaded, f"执行中断: {type(exc).__name__}")


def _execute(
    db: Session, agent_run: AgentRun, ticket: Ticket, propose_intent
) -> AgentRun:
    """按 Golden Path 顺序执行各步骤，任何一步未达成成功结果即终止。"""
    # --- 步骤 1：提取客户意图（T011 的 validation boundary） ---
    intent_outcome = extract_and_persist_intent(db, agent_run, propose_intent(ticket))
    if (
        intent_outcome.status is not IntentExtractionStatus.SUCCESS
        or intent_outcome.intent is None
    ):
        return _fail_run(
            db,
            agent_run,
            _step_failure(
                INTENT_STEP_NAME,
                intent_outcome.status.value,
                intent_outcome.failure_reason,
            ),
        )
    intent = intent_outcome.intent

    # --- 步骤 2：检索售后政策（T012） ---
    policy_result = lookup_replacement_policy(db, intent)
    policy_ok = policy_result.status is PolicyLookupStatus.SUCCESS
    _record_step(
        db,
        agent_run,
        POLICY_STEP_ORDER,
        POLICY_STEP_NAME,
        policy_ok,
        None
        if policy_ok
        else _tool_failure(policy_result.status.value, policy_result.failure_reason),
    )
    if not policy_ok:
        return _fail_run(
            db,
            agent_run,
            _step_failure(
                POLICY_STEP_NAME,
                policy_result.status.value,
                policy_result.failure_reason,
            ),
        )

    # --- 步骤 3：查询订单信息（T013） ---
    order_request = _build_order_request(ticket)
    if order_request is None:
        _record_step(
            db,
            agent_run,
            ORDER_STEP_ORDER,
            ORDER_STEP_NAME,
            False,
            "reason=工单未关联可查询的订单标识",
        )
        return _fail_run(
            db,
            agent_run,
            f"{ORDER_STEP_NAME}失败: 工单 {ticket.business_key} 未关联可查询的订单",
        )

    order_result = get_order(db, order_request)
    order_ok = order_result.status is OrderLookupStatus.SUCCESS
    _record_step(
        db,
        agent_run,
        ORDER_STEP_ORDER,
        ORDER_STEP_NAME,
        order_ok,
        None
        if order_ok
        else _tool_failure(order_result.status.value, order_result.failure_reason),
    )
    if not order_ok or order_result.order is None:
        return _fail_run(
            db,
            agent_run,
            _step_failure(
                ORDER_STEP_NAME,
                order_result.status.value,
                order_result.failure_reason,
            ),
        )
    order_facts = order_result.order

    # --- 步骤 4：检查换货库存（T014） ---
    # 查询用的 SKU 取自上一步真实返回的订单事实，而不是工单上的任何副本
    inventory_result = check_and_persist_inventory(
        db, agent_run, CheckInventoryRequest(product_sku=order_facts.product_sku)
    )
    # 有货与无货都是库存 Tool 的真实成功返回；无货由下一步的判定确定性阻断
    if inventory_result.status not in (
        InventoryCheckStatus.SUCCESS,
        InventoryCheckStatus.UNAVAILABLE,
    ):
        return _fail_run(
            db,
            agent_run,
            _step_failure(
                INVENTORY_CHECK_STEP_NAME,
                inventory_result.status.value,
                inventory_result.failure_reason,
            ),
        )

    # --- 步骤 5：评估换货资格（T015） ---
    decision = decide_replacement(
        intent_outcome, policy_result, order_result, inventory_result
    )
    decision_ok = decision.status is ReplacementDecisionStatus.ELIGIBLE
    _record_step(
        db,
        agent_run,
        DECISION_STEP_ORDER,
        DECISION_STEP_NAME,
        decision_ok,
        None
        if decision_ok
        else (
            f"status={decision.status.value}; "
            f"reason_code={decision.reason_code.value}; reason={decision.reason}"
        ),
    )
    if not decision_ok:
        return _fail_run(
            db,
            agent_run,
            (
                f"{DECISION_STEP_NAME}未通过: status={decision.status.value}; "
                f"reason_code={decision.reason_code.value}; reason={decision.reason}"
            ),
        )

    # --- 步骤 6：创建换货单（T016，第一个真实业务状态变更） ---
    replacement_result = create_replacement(
        db,
        agent_run,
        CreateReplacementRequest(
            order_key=order_facts.order_key,
            product_sku=order_facts.product_sku,
            reason=intent.issue_summary,
        ),
        decision,
    )
    if (
        replacement_result.status is not CreateReplacementStatus.CREATED
        or replacement_result.replacement is None
    ):
        return _fail_run(
            db,
            agent_run,
            _step_failure(
                REPLACEMENT_STEP_NAME,
                replacement_result.status.value,
                replacement_result.failure_reason,
            ),
        )
    replacement = replacement_result.replacement

    # --- 步骤 7：回写工单结果（T017，唯一能让 Run 进入 completed 的路径） ---
    update_result = update_ticket(
        db,
        agent_run,
        UpdateTicketRequest(
            ticket_key=ticket.business_key,
            resolution=TicketResolution.REPLACEMENT_CREATED,
            replacement_key=replacement.replacement_key,
            summary=_resolution_summary(replacement, order_facts, intent.issue_summary),
        ),
    )
    if update_result.status is not UpdateTicketStatus.UPDATED:
        return _fail_run(
            db,
            agent_run,
            _step_failure(
                UPDATE_TICKET_STEP_NAME,
                update_result.status.value,
                update_result.failure_reason,
            ),
        )

    db.refresh(agent_run)
    return agent_run


def _resolution_summary(replacement, order_facts, issue_summary: str) -> str:
    """用真实执行结果拼出工单结果摘要。

    摘要里的换货单标识、订单标识与商品都取自刚刚落库的那张换货单与真实订单事实，
    不含任何由模型生成的结论性描述。
    """
    summary = (
        f"已为订单 {order_facts.order_key} 创建换货单 "
        f"{replacement.replacement_key}（商品 {replacement.product_sku}）。"
        f"客户问题：{issue_summary}"
    )
    return summary[:_MAX_SUMMARY_LENGTH]


def _build_order_request(ticket: Ticket) -> GetOrderRequest | None:
    """把工单关联的订单标识包装成已验证的 typed 请求。

    工单没有关联订单、或订单标识不符合 Tool 契约时返回 ``None``，由调用方记录一次
    真实的步骤失败，而不是让异常穿透成 HTTP 500。
    """
    order = ticket.order
    if order is None or not order.business_key:
        return None
    try:
        return GetOrderRequest(order_key=order.business_key)
    except ValueError:
        return None


def _create_agent_run(db: Session, ticket: Ticket) -> AgentRun:
    """为该工单创建一次新的、已持久化的 Agent Run。

    business_key 由工单标识加序号确定性派生；并发下若发生冲突，由数据库唯一约束
    拒绝并在此重试，绝不复用别人的 Run。
    """
    for attempt in range(_MAX_RUN_KEY_ATTEMPTS):
        sequence = (
            db.query(AgentRun).filter(AgentRun.ticket_id == ticket.id).count()
            + 1
            + attempt
        )
        agent_run = AgentRun(
            business_key=_run_key(ticket.business_key, sequence),
            ticket_id=ticket.id,
            status=AgentRunStatus.QUEUED,
        )
        db.add(agent_run)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(agent_run)
        return agent_run

    raise RuntimeError(f"无法为工单 {ticket.business_key} 分配唯一的 Agent Run 标识")


def _run_key(ticket_key: str, sequence: int) -> str:
    """确定性派生 Run 标识，并保证不超出 business_key 的列宽。"""
    return f"{RUN_KEY_PREFIX}{ticket_key[:_MAX_TICKET_KEY_IN_RUN_KEY]}-{sequence:03d}"


def _record_step(
    db: Session,
    agent_run: AgentRun,
    step_order: int,
    name: str,
    succeeded: bool,
    error_message: str | None,
) -> AgentStep:
    """在一次调用真实返回之后记录该步骤的终态。

    只在调用方拿到真实结果之后才被调用，因此步骤不存在"先成功后执行"的可能。
    记录方式与 intent / inventory / replacement / ticket 四个既有服务保持一致。
    """
    step = (
        db.query(AgentStep)
        .filter(AgentStep.agent_run_id == agent_run.id, AgentStep.name == name)
        .one_or_none()
    )
    if step is None:
        step = AgentStep(agent_run_id=agent_run.id, step_order=step_order, name=name)
        db.add(step)

    step.status = AgentStepStatus.COMPLETED if succeeded else AgentStepStatus.FAILED
    step.completed_at = datetime.utcnow()
    step.error_message = None if succeeded else error_message
    db.commit()
    return step


def _fail_run(db: Session, agent_run: AgentRun, error_message: str) -> AgentRun:
    """把 Run 置为失败终态。

    本模块只能把 Run 置为 ``FAILED``；``COMPLETED`` 的唯一来源仍然是
    ``ticket_service`` 在工单真正回写之后的置位。
    """
    agent_run.status = AgentRunStatus.FAILED
    agent_run.completed_at = datetime.utcnow()
    agent_run.error_message = error_message
    db.commit()
    db.refresh(agent_run)
    return agent_run


def _step_failure(step_name: str, status: str, reason: str | None) -> str:
    """把某一步的真实失败结果编码成 Run 级别的结构化失败原因。"""
    message = f"{step_name}失败: status={status}"
    if reason:
        message = f"{message}; reason={reason}"
    return message


def _tool_failure(status: str, reason: str | None) -> str:
    """把 Tool 的真实失败结果编码成步骤级别的结构化失败原因。"""
    parts = [f"status={status}"]
    if reason:
        parts.append(f"reason={reason}")
    return "; ".join(parts)
