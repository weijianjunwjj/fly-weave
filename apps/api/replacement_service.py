"""create_replacement Tool 的 application / business mutation boundary。

这是 Golden Path 上第一个真正改变业务状态的边界。前面几个 Tool 只读取事实，
本服务会在数据库事务中写入一张真实的换货单：

    Agent / workflow
        → ReplacementDecision（T015 的 eligible 判定）
        → CreateReplacementRequest（已验证）
        → 本服务对照持久化状态逐条校验前置条件
        → 事务内写入 ReplacementOrder
        → CreateReplacementResult

"换货成功"这一事实的唯一来源是数据库里那一行 ``ReplacementOrder``。模型文本
既不能触发写入，也不能把结果置为 created：任何一条前置条件不满足，本服务都
返回结构化失败，且不会留下任何半成品状态。

前置条件刻意在执行时刻重新对照持久化状态求值，而不是照单全收 T015 的判定：
判定与执行之间订单可能被取消、库存可能被清零。判定负责"依据证据是否有资格"，
本服务负责"此刻是否真的可以执行"，两者都必须通过。

不在本任务范围内：工单写回（T017）、审批闸门与审批流程（T019 / T020）、
库存扣减（当前 domain 没有库存预留 / 出库概念，不预先编造）。
"""
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from decision_service import REPLACEABLE_ORDER_STATUSES
from decisions import ReplacementDecision, ReplacementDecisionStatus
from models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    InventoryItem,
    Order,
    ReplacementOrder,
    ReplacementStatus,
    Ticket,
)
from replacements import (
    CreateReplacementRequest,
    CreateReplacementResult,
    CreateReplacementStatus,
    ReplacementRecord,
)

# create_replacement 在 Golden Path 中的步骤序号。intent=1、inventory=4 已被
# 占用，2 / 3 / 5 分别为后续 policy / order / decision 步骤保留。
REPLACEMENT_STEP_NAME = "创建换货单"
REPLACEMENT_STEP_ORDER = 6

# 换货单业务标识的确定性前缀：同一订单的换货单标识稳定可预期，
# 与 order_id 唯一约束表达的是同一条业务规则（一个订单至多一张换货单）。
REPLACEMENT_KEY_PREFIX = "replacement-"


def create_replacement(
    db: Session,
    agent_run: AgentRun,
    request: CreateReplacementRequest,
    decision: ReplacementDecision,
) -> CreateReplacementResult:
    """校验全部前置条件后，为一个 eligible 的换货案例创建真实换货单。

    校验顺序由"最不可信的输入"到"最权威的持久化状态"：先拒绝非 typed 输入与
    非 eligible 判定，再确认判定与本次请求说的是同一个案子，最后对照数据库里
    订单、工单、Run、库存的真实状态，并以持久化状态判定是否重复。

    只有全部通过才写入换货单并提交；任何一步失败都返回结构化结果，同时把这次
    真实的 Tool 调用结果记录到 Agent Run 的步骤中。
    """
    outcome = _validate_preconditions(db, agent_run, request, decision)
    if isinstance(outcome, CreateReplacementResult):
        return _finish(db, agent_run, outcome)

    order, ticket, inventory_item = outcome
    replacement = ReplacementOrder(
        business_key=f"{REPLACEMENT_KEY_PREFIX}{order.business_key}",
        order_id=order.id,
        ticket_id=ticket.id,
        agent_run_id=agent_run.id,
        product_sku=order.product_sku,
        reason=request.reason,
        status=ReplacementStatus.CREATED,
        # 演示标记随订单，不独立编造
        is_demo_data=order.is_demo_data,
    )
    db.add(replacement)

    try:
        # flush 让数据库层的唯一约束在此刻表态：即使两次执行并发穿过了上面的
        # 重复检查，也只有一次能真正写入
        db.flush()
    except IntegrityError:
        db.rollback()
        return _finish_after_conflict(db, agent_run.id, request)

    result = CreateReplacementResult(
        status=CreateReplacementStatus.CREATED,
        replacement=_record_view(replacement, order, ticket, agent_run),
    )
    return _finish(db, agent_run, result)


def _validate_preconditions(
    db: Session,
    agent_run: AgentRun,
    request: CreateReplacementRequest,
    decision: ReplacementDecision,
):
    """逐条校验前置条件。

    全部通过时返回 ``(order, ticket, inventory_item)`` 三个真实持久化实体，
    供调用方直接建立业务关联；任何一条不满足则返回结构化失败结果。
    """
    # --- 1. 输入必须是已验证的 typed 请求 ---
    if not isinstance(request, CreateReplacementRequest):
        return _failure(
            CreateReplacementStatus.INVALID_REQUEST,
            "create_replacement 只接受已验证的 CreateReplacementRequest，"
            "不接受模型原始文本",
        )

    # --- 2. 执行权限只来自 T015 的 eligible 判定 ---
    if not isinstance(decision, ReplacementDecision):
        return _failure(
            CreateReplacementStatus.NOT_ELIGIBLE,
            "create_replacement 只接受已验证的 ReplacementDecision，"
            "不接受模型原始文本",
        )
    if decision.status is not ReplacementDecisionStatus.ELIGIBLE:
        return _failure(
            CreateReplacementStatus.NOT_ELIGIBLE,
            f"换货判定为 {decision.status.value}"
            f"（{decision.reason_code.value}），不得执行换货",
        )

    # --- 3. 判定与请求必须说的是同一个案子 ---
    # ELIGIBLE 的判定在契约层已保证这两个证据字段非空
    if decision.evidence.order.order_key != request.order_key:
        return _failure(
            CreateReplacementStatus.EVIDENCE_MISMATCH,
            f"判定依据的订单 {decision.evidence.order.order_key} "
            f"与请求订单 {request.order_key} 不一致",
        )
    if decision.evidence.inventory.product_sku != request.product_sku:
        return _failure(
            CreateReplacementStatus.EVIDENCE_MISMATCH,
            f"判定依据的商品 {decision.evidence.inventory.product_sku} "
            f"与请求商品 {request.product_sku} 不一致",
        )

    # --- 4. 订单必须真实存在 ---
    order = (
        db.query(Order).filter(Order.business_key == request.order_key).one_or_none()
    )
    if order is None:
        return _failure(
            CreateReplacementStatus.ORDER_NOT_FOUND,
            f"未找到订单: {request.order_key}",
        )
    if order.product_sku != request.product_sku:
        return _failure(
            CreateReplacementStatus.EVIDENCE_MISMATCH,
            f"订单 {order.business_key} 的商品为 {order.product_sku}，"
            f"与请求商品 {request.product_sku} 不一致",
        )

    # --- 5. 执行时刻订单状态必须仍然允许换货 ---
    # 口径与 T015 共用同一个常量，避免判定与执行出现两套规则
    if order.status not in REPLACEABLE_ORDER_STATUSES:
        return _failure(
            CreateReplacementStatus.ORDER_NOT_REPLACEABLE,
            f"订单 {order.business_key} 当前状态为 {order.status.value}，"
            "不允许换货",
        )

    # --- 6. Run 必须与该订单处在同一业务上下文 ---
    if not isinstance(agent_run, AgentRun) or agent_run.id is None:
        return _failure(
            CreateReplacementStatus.RUN_LINKAGE_INVALID,
            "create_replacement 必须由一次已持久化的 Agent Run 执行",
        )
    ticket = db.query(Ticket).filter(Ticket.id == agent_run.ticket_id).one_or_none()
    if ticket is None:
        return _failure(
            CreateReplacementStatus.RUN_LINKAGE_INVALID,
            f"Agent Run {agent_run.business_key} 关联的工单不存在",
        )
    if ticket.order_id != order.id:
        return _failure(
            CreateReplacementStatus.RUN_LINKAGE_INVALID,
            f"Agent Run {agent_run.business_key} 关联的工单 "
            f"{ticket.business_key} 并不对应订单 {order.business_key}",
        )

    # --- 7. 执行时刻库存必须真的有货 ---
    # 与 T014 的可用性语义一致：数量为零是"无货"这一确定性事实，查无此 SKU
    # 同样无法支撑换货执行
    inventory_item = (
        db.query(InventoryItem)
        .filter(InventoryItem.product_sku == request.product_sku)
        .one_or_none()
    )
    if inventory_item is None:
        return _failure(
            CreateReplacementStatus.INVENTORY_UNAVAILABLE,
            f"未找到库存: {request.product_sku}",
        )
    if inventory_item.available_quantity <= 0:
        return _failure(
            CreateReplacementStatus.INVENTORY_UNAVAILABLE,
            f"商品 {inventory_item.product_sku} 在仓库 "
            f"{inventory_item.warehouse} 可用数量为 "
            f"{inventory_item.available_quantity}，无法换货",
        )

    # --- 8. 重复执行由持久化状态判定，而非进程内状态 ---
    existing = _find_existing_replacement(db, order.id, agent_run.id)
    if existing is not None:
        return _duplicate(existing, order.business_key)

    return order, ticket, inventory_item


def _find_existing_replacement(
    db: Session, order_id: int, agent_run_id: int
) -> ReplacementOrder | None:
    """查找已经存在的换货单。

    订单维度与 Run 维度任一命中都算重复：同一订单不得换两次，同一次 Run 也不得
    把这个受保护动作执行两次。两者在数据库层都有唯一约束兜底。
    """
    return (
        db.query(ReplacementOrder)
        .filter(
            (ReplacementOrder.order_id == order_id)
            | (ReplacementOrder.agent_run_id == agent_run_id)
        )
        .first()
    )


def _finish_after_conflict(
    db: Session, agent_run_id: int, request: CreateReplacementRequest
) -> CreateReplacementResult:
    """唯一约束拒绝写入后，把冲突诚实地表示为 duplicate。

    走到这里说明并发的另一次执行抢先写入了换货单。回滚之后重新查询持久化状态，
    以数据库里真实存在的那一张换货单为准。
    """
    order = (
        db.query(Order).filter(Order.business_key == request.order_key).one_or_none()
    )
    existing = (
        _find_existing_replacement(db, order.id, agent_run_id)
        if order is not None
        else None
    )
    if existing is None:
        # 冲突已由数据库确认，却查不到对应换货单：不猜测原因，也绝不据此宣称成功
        result = _failure(
            CreateReplacementStatus.INVALID_REQUEST,
            f"换货单写入被数据库唯一约束拒绝: {request.order_key}",
        )
    else:
        result = _duplicate(existing, request.order_key)

    agent_run = db.query(AgentRun).filter(AgentRun.id == agent_run_id).one_or_none()
    if agent_run is None:
        return result
    return _finish(db, agent_run, result)


def _duplicate(
    existing: ReplacementOrder, order_key: str
) -> CreateReplacementResult:
    """把已存在的换货单表示为结构化的重复结果，并指出它的标识。"""
    return CreateReplacementResult(
        status=CreateReplacementStatus.DUPLICATE,
        existing_replacement_key=existing.business_key,
        failure_reason=(
            f"订单 {order_key} 已存在换货单 {existing.business_key}，"
            "本次执行被拒绝"
        ),
    )


def _failure(
    status: CreateReplacementStatus, reason: str
) -> CreateReplacementResult:
    """构造结构化失败结果。失败永远不携带换货单。"""
    return CreateReplacementResult(status=status, failure_reason=reason)


def _record_view(
    replacement: ReplacementOrder,
    order: Order,
    ticket: Ticket,
    agent_run: AgentRun,
) -> ReplacementRecord:
    """把已写入的换货单映射为类型化视图，三条业务关联全部用稳定业务标识表示。"""
    return ReplacementRecord(
        replacement_key=replacement.business_key,
        status=replacement.status,
        order_key=order.business_key,
        ticket_key=ticket.business_key,
        agent_run_key=agent_run.business_key,
        product_sku=replacement.product_sku,
        reason=replacement.reason,
        is_demo_data=replacement.is_demo_data,
        created_at=replacement.created_at,
    )


def _finish(
    db: Session, agent_run: AgentRun, result: CreateReplacementResult
) -> CreateReplacementResult:
    """把本次真实执行结果记录到 Agent Run 并提交事务。

    记录遵循 intent_service / inventory_service 的既有惯例：把 Run 从 queued
    提升为 running，并在步骤上如实反映结果——只有真正写入换货单才是 completed，
    重复与各类前置条件失败一律是 failed，绝不把失败显示成成功。
    """
    _mark_run_started(agent_run)
    step = _get_or_create_replacement_step(db, agent_run)
    step.completed_at = datetime.utcnow()

    if result.status is CreateReplacementStatus.CREATED:
        step.status = AgentStepStatus.COMPLETED
        step.error_message = None
    else:
        step.status = AgentStepStatus.FAILED
        step.error_message = _format_failure_message(result)

    db.commit()
    return result


def _mark_run_started(agent_run: AgentRun) -> None:
    """一次 Run 开始执行时，将 queued 提升为 running 并记录开始时间。

    与既有服务保持一致：只负责"已经开始"这一事实，Run 最终是否 completed /
    failed 由后续端到端编排决定。
    """
    if agent_run.status is AgentRunStatus.QUEUED:
        agent_run.status = AgentRunStatus.RUNNING
        agent_run.started_at = datetime.utcnow()


def _get_or_create_replacement_step(db: Session, agent_run: AgentRun) -> AgentStep:
    """取回该 Run 已有的换货步骤，或创建一条 running 起步的步骤记录。"""
    step = (
        db.query(AgentStep)
        .filter(
            AgentStep.agent_run_id == agent_run.id,
            AgentStep.name == REPLACEMENT_STEP_NAME,
        )
        .one_or_none()
    )
    if step is None:
        step = AgentStep(
            agent_run_id=agent_run.id,
            step_order=REPLACEMENT_STEP_ORDER,
            name=REPLACEMENT_STEP_NAME,
            status=AgentStepStatus.RUNNING,
        )
        db.add(step)
        db.flush()
    return step


def _format_failure_message(result: CreateReplacementResult) -> str:
    """把失败结果编码成可检查的结构化失败原因，写入 AgentStep.error_message。"""
    parts = [f"status={result.status.value}"]
    if result.existing_replacement_key:
        parts.append(f"existing={result.existing_replacement_key}")
    if result.failure_reason:
        parts.append(f"reason={result.failure_reason}")
    return "; ".join(parts)
