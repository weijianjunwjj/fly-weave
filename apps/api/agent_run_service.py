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

不在本任务范围内：审批结果（approve / reject，T020）。T019 的确定性风险门禁由
``replacement_service`` 在受保护动作真正写入之前执行，本模块只把它的命中结果
原样映射为 ``WAITING_FOR_APPROVAL`` 暂停状态，不做任何自动放行或拒绝。
``ReplacementDecision.policy_approval_threshold_exceeded`` 是 T015 携带的事实
标记，风险门禁独立基于执行时持久化事实重新求值，本模块仍不因它暂停、分流或
拒绝执行。
"""
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from approval_service import approval_authorizes, get_approval_request
from approvals import ApprovalRequestStatus
from audit_service import record_audit_event
from decision_service import decide_replacement
from decisions import ReplacementDecisionStatus
from intent_proposal import propose_replacement_intent
from intent_service import INTENT_STEP_NAME, extract_and_persist_intent
from intents import (
    IntentExtractionOutcome,
    IntentExtractionStatus,
    IntentType,
    RequestedAction,
    ReplacementIntent,
)
from inventory import CheckInventoryRequest, InventoryCheckStatus
from inventory_service import (
    INVENTORY_CHECK_STEP_NAME,
    check_and_persist_inventory,
    check_inventory,
)
from models import (
    ActorType,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    ApprovalRequest,
    AuditEventType,
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


class AgentRunNotFoundError(Exception):
    """恢复执行时找不到对应的 AgentRun。"""

    def __init__(self, agent_run_key: str) -> None:
        super().__init__(f"未找到 AgentRun: {agent_run_key}")
        self.agent_run_key = agent_run_key


class ApprovalNotFoundError(Exception):
    """该 Run 的受保护动作不存在任何审批请求。"""

    def __init__(self, agent_run_key: str) -> None:
        super().__init__(f"AgentRun {agent_run_key} 没有可恢复的审批请求")
        self.agent_run_key = agent_run_key


class ResumeConflictError(Exception):
    """审批请求或 Run 的当前状态不允许恢复执行。"""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


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
    # T023：get_order 已真实执行，结果直接来自本次查询；审计记录的是这次查询的
    # 真实状态，而不是一句"订单已查到"的结论。
    record_audit_event(
        db,
        agent_run=agent_run,
        event_type=AuditEventType.GET_ORDER,
        actor_type=ActorType.AGENT,
        outcome=order_result.status.value,
        success=order_ok,
        action="get_order",
        summary=(
            f"查询订单: order={order_result.order.order_key}"
            if order_ok and order_result.order is not None
            else f"查询订单失败: status={order_result.status.value}"
        ),
        affected_object_type="order",
        affected_object_key=(
            order_result.order.order_key
            if order_result.order is not None
            else order_result.requested_order_key
        ),
    )
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
    # T023：结构化换货判定已真实产生，结果与理由码直接来自 decision，不重新推导。
    record_audit_event(
        db,
        agent_run=agent_run,
        event_type=AuditEventType.DECISION_PRODUCED,
        actor_type=ActorType.AGENT,
        outcome=decision.status.value,
        success=decision_ok,
        action="decide_replacement",
        summary=(
            f"换货资格判定: status={decision.status.value} "
            f"reason_code={decision.reason_code.value}"
        ),
        affected_object_type="order",
        affected_object_key=order_facts.order_key,
        metadata={"reason_code": decision.reason_code.value},
    )
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
    if replacement_result.status is CreateReplacementStatus.APPROVAL_REQUIRED:
        # T019 风险门禁命中：受保护动作被拦下，Run 进入等待人工审批而不是失败。
        # 本模块不实现 approve / reject，因此 Run 就停在这个暂停状态。
        return _await_approval(db, agent_run)
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
    # T023：失败终态只在这里与 ticket_service 的 completed 两个入口产生。审计
    # 记录的是真实持久化终态，而不是 error_message 里的一段文本复述。
    record_audit_event(
        db,
        agent_run=agent_run,
        event_type=AuditEventType.AGENT_RUN_OUTCOME,
        actor_type=ActorType.AGENT,
        outcome=AgentRunStatus.FAILED.value,
        success=False,
        action="agent_run_outcome",
        summary="AgentRun 终态: status=failed",
        affected_object_type="agent_run",
        affected_object_key=agent_run.business_key,
    )
    db.commit()
    db.refresh(agent_run)
    return agent_run


def _await_approval(db: Session, agent_run: AgentRun) -> AgentRun:
    """把 Run 置为等待人工审批的暂停终态（T019 风险门禁命中）。

    这是"受保护动作被确定性风险规则拦下"之后的诚实状态：Run 没有失败（没有任何
    前置条件不满足），也没有完成（换货没有发生、工单没有回写）。它停在
    ``WAITING_FOR_APPROVAL``，等待后续的 approve / reject 流程 —— 而 approve /
    reject 不在本任务范围内，因此本模块不会继续推进它。

    风险原因本身已由 ``replacement_service`` 写入该 Run 的换货步骤；T020 的
    ``ApprovalRequest`` 也在 ``replacement_service`` 的同一事务里与 waiting 状态
    一起建立，因此这里只是再次把 Run 稳定在等待审批，不会出现"等审批却没有审批
    请求"的可提交状态。Run 的 ``error_message`` 保持为空，因为它不是失败。
    """
    agent_run.status = AgentRunStatus.WAITING_FOR_APPROVAL
    agent_run.completed_at = None
    agent_run.error_message = None
    db.commit()
    db.refresh(agent_run)
    return agent_run


def resume_agent_run(db: Session, agent_run_key: str) -> AgentRun:
    """对一条已 APPROVED 的高风险 Run 恢复执行其受保护动作并完成闭环（T022）。

    approve（T021）与本函数是两个**独立**动作：approve 只把审批请求记录为
    APPROVED，绝不自动恢复执行；本函数是紧随其后、由调用方显式触发的 Resume。
    授权事实只来自数据库里那条 durable ApprovalRequest，绝不来自任何请求参数。

    返回的 ``AgentRun`` 状态就是恢复执行后的真实终态：只有换货单真实落库且工单
    真实回写之后才是 ``COMPLETED``，任何失败都是 ``FAILED``；等待 / 已拒绝 /
    不匹配一律抛异常，由 API 层映射为 404 / 409，本函数不做任何业务副作用。
    """
    agent_run = _load_agent_run(db, agent_run_key)
    if agent_run is None:
        raise AgentRunNotFoundError(agent_run_key)
    if agent_run.status is AgentRunStatus.COMPLETED:
        # 幂等：已经完成的 Run 直接返回当前持久化状态，绝不重新执行业务动作。
        return agent_run
    if agent_run.status is not AgentRunStatus.WAITING_FOR_APPROVAL:
        raise ResumeConflictError(
            f"AgentRun {agent_run_key} 当前状态为 {agent_run.status.value}，无法恢复执行"
        )

    # --- 审批守卫：授权只能来自持久化的 APPROVED ApprovalRequest ---
    approval = get_approval_request(db, agent_run)
    if approval is None:
        raise ApprovalNotFoundError(agent_run_key)
    if approval.status is ApprovalRequestStatus.PENDING:
        raise ResumeConflictError(
            f"审批请求 {approval.business_key} 仍为 pending，尚未获准恢复执行"
        )
    if approval.status is ApprovalRequestStatus.REJECTED:
        raise ResumeConflictError(
            f"审批请求 {approval.business_key} 已被拒绝，永久阻止恢复执行"
        )

    # --- 从持久化事实重建业务上下文（只读，不产生副作用） ---
    context = _reconstruct_resume_context(db, agent_run)
    if context is None:
        return _fail_run(db, agent_run, "恢复执行失败: 无法从持久化状态重建业务上下文")
    intent, order_facts, policy_key, decision = context

    # --- 绑定校验：审批必须精确匹配当前 action / Run / 订单身份 / 政策身份 ---
    if not approval_authorizes(approval, agent_run, order_facts.order_key, policy_key):
        raise ResumeConflictError(
            f"审批请求 {approval.business_key} 与当前业务上下文不匹配，拒绝执行"
        )

    # --- 数据库级 compare-and-set 领取执行权：并发 Resume 至多一个成功 ---
    if not _claim_resume(db, agent_run_key):
        db.expire_all()
        reloaded = _load_agent_run(db, agent_run_key)
        if reloaded is None:
            raise AgentRunNotFoundError(agent_run_key)
        if reloaded.status is AgentRunStatus.COMPLETED:
            return reloaded
        raise ResumeConflictError(
            f"AgentRun {agent_run_key} 正在被另一恢复执行处理，本次放弃"
        )

    agent_run = _load_agent_run(db, agent_run_key)
    try:
        return _execute_resume(db, agent_run, approval, intent, order_facts, decision)
    except Exception as exc:  # noqa: BLE001 - 恢复中断不得让 Run 悬停在 running
        db.rollback()
        reloaded = _load_agent_run(db, agent_run_key)
        if reloaded is None:
            raise
        return _fail_run(db, reloaded, f"恢复执行中断: {type(exc).__name__}")


def _load_agent_run(db: Session, agent_run_key: str) -> AgentRun | None:
    """按业务标识取回一次 Agent Run，不存在则返回 ``None``。"""
    return (
        db.query(AgentRun)
        .filter(AgentRun.business_key == agent_run_key)
        .one_or_none()
    )


def _reconstruct_resume_context(db: Session, agent_run: AgentRun):
    """从持久化事实重建恢复执行所需的业务上下文，失败返回 ``None``。

    只读、不产生副作用：intent 来自已落库的 ``AgentIntent``，政策 / 订单 / 库存
    来自当前持久化状态，decision 由 T015 的确定性判定重新求值。这里**不重新让
    LLM 决定业务规则**——一切依据都是已经持久化或应用拥有的确定性事实。
    """
    ticket = agent_run.ticket
    order = ticket.order if ticket is not None else None
    agent_intent = agent_run.intent
    if order is None or agent_intent is None:
        return None

    try:
        intent = ReplacementIntent(
            intent_type=IntentType(agent_intent.intent_type),
            requested_action=RequestedAction(agent_intent.requested_action),
            issue_summary=agent_intent.issue_summary,
            confidence=agent_intent.confidence,
        )
    except (ValueError, TypeError):
        return None

    policy_result = lookup_replacement_policy(db, intent)
    if (
        policy_result.status is not PolicyLookupStatus.SUCCESS
        or policy_result.source is None
    ):
        return None

    order_result = get_order(db, GetOrderRequest(order_key=order.business_key))
    if order_result.status is not OrderLookupStatus.SUCCESS or order_result.order is None:
        return None

    inventory_result = check_inventory(
        db, CheckInventoryRequest(product_sku=order_result.order.product_sku)
    )
    decision = decide_replacement(
        IntentExtractionOutcome(
            status=IntentExtractionStatus.SUCCESS, intent=intent
        ),
        policy_result,
        order_result,
        inventory_result,
    )
    return intent, order_result.order, policy_result.source.policy_key, decision


def _claim_resume(db: Session, agent_run_key: str) -> bool:
    """数据库级 compare-and-set：只有 waiting_for_approval 的 Run 能被领取。

    与 T021 的审批 CAS 同一思路：WHERE 里的 status = 'waiting_for_approval' 是
    原子条件，两个并发 Resume 至多一个得到 rowcount == 1。失败者随后以数据库真实
    状态判定为幂等（已 completed）或冲突（正在被另一恢复执行处理）。
    """
    updated = (
        db.query(AgentRun)
        .filter(
            AgentRun.business_key == agent_run_key,
            AgentRun.status == AgentRunStatus.WAITING_FOR_APPROVAL,
        )
        .update({AgentRun.status: AgentRunStatus.RUNNING}, synchronize_session=False)
    )
    db.commit()
    return updated == 1


def _execute_resume(
    db: Session,
    agent_run: AgentRun,
    approval: ApprovalRequest,
    intent: ReplacementIntent,
    order_facts,
    decision,
) -> AgentRun:
    """成功领取恢复权后，执行受保护动作（步骤 6）与工单回写（步骤 7）。"""
    request = CreateReplacementRequest(
        order_key=order_facts.order_key,
        product_sku=order_facts.product_sku,
        reason=intent.issue_summary,
    )
    replacement_result = create_replacement(
        db, agent_run, request, decision, authorization=approval
    )
    if replacement_result.status is not CreateReplacementStatus.CREATED:
        # APPROVAL_REQUIRED / AUTHORIZATION_MISMATCH / NOT_ELIGIBLE / 各类前置条件
        # 失败 / DUPLICATE：一律不得继续，如实置为失败，绝不让工单被错误标记成功。
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

    update_result = update_ticket(
        db,
        agent_run,
        UpdateTicketRequest(
            ticket_key=agent_run.ticket.business_key,
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
