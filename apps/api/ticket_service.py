"""update_ticket Tool 的 application / business mutation boundary。

Golden Path 上的最后一个写入边界：把一次已经真实发生的换货执行回写到售后工单。

    Agent / workflow
        → 已持久化的 ReplacementOrder（T016 的真实执行结果）
        → UpdateTicketRequest（已验证）
        → 本服务对照持久化状态校验工单、换货单与 Run 的关联
        → 事务内更新 Ticket 的状态与结果引用
        → UpdateTicketResult

两条不可让步的性质：

1. **结果必须有实据。** 回写引用的换货单必须能在数据库中查到，并且必须属于本
   工单、由本次 Run 执行。模型说"换货已完成"不构成结案依据，只有那一行
   ``ReplacementOrder`` 可以。
2. **只有回写成功，Run 才算完成。** 本服务是当前系统中唯一把 ``AgentRun`` 置为
   ``COMPLETED`` 的地方，且只在工单真正落库之后。任何一条校验不通过、或写入被
   持久化层拒绝，Run 一律保持非 completed，绝不出现"工单没改，Run 却显示完成"。

不在本任务范围内：端到端流程编排（T018）、审批闸门（T019 / T020）。本服务只在
被显式调用时执行一次回写，不驱动流程、不推进后续步骤。失败时 Run 停留在
running，最终失败终态由后续端到端编排判定，这与 intent / inventory /
replacement 三个服务的既有语义一致。
"""
from datetime import datetime

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    ReplacementOrder,
    Ticket,
    TicketStatus,
)
from tickets import (
    TicketRecord,
    UpdateTicketRequest,
    UpdateTicketResult,
    UpdateTicketStatus,
)

# update_ticket 在 Golden Path 中的步骤序号。紧随 create_replacement（6）之后，
# 是这条路径上最后一个真实副作用。
UPDATE_TICKET_STEP_NAME = "回写工单结果"
UPDATE_TICKET_STEP_ORDER = 7


def update_ticket(
    db: Session, agent_run: AgentRun, request: UpdateTicketRequest
) -> UpdateTicketResult:
    """把一次已确认执行成功的换货结果回写到工单。

    校验顺序由"最不可信的输入"到"最权威的持久化状态"：先拒绝非 typed 输入，
    再确认工单与 Run 处在同一业务上下文，最后要求所引用的换货单在数据库中真实
    存在、且确实由本次 Run 为本工单执行。

    只有全部通过才更新工单并提交，并在此之后才允许 Run 进入 completed；任何一步
    失败都返回结构化结果，工单保持原状，Run 保持非 completed。
    """
    outcome = _validate_preconditions(db, agent_run, request)
    if isinstance(outcome, UpdateTicketResult):
        return _finish(db, agent_run, outcome)

    ticket, replacement = outcome
    ticket.status = TicketStatus.RESOLVED
    ticket.resolution = request.resolution
    ticket.resolution_summary = request.summary
    # 引用的是刚刚校验过的那一行换货单的真实主键，而不是请求里的字符串
    ticket.replacement_id = replacement.id
    ticket.resolved_at = datetime.utcnow()

    try:
        # flush 让数据库层在此刻表态（外键、唯一约束），而不是等到 commit 之后
        # 才发现工单其实没写进去
        db.flush()
    except SQLAlchemyError as exc:
        # 写入被持久化层拒绝：回滚使工单保持回写前的状态，Run 也不会 completed
        db.rollback()
        return _finish_after_persistence_failure(db, agent_run, request, exc)

    result = UpdateTicketResult(
        status=UpdateTicketStatus.UPDATED,
        ticket=_record_view(ticket, replacement),
    )
    return _finish(db, agent_run, result)


def _validate_preconditions(
    db: Session, agent_run: AgentRun, request: UpdateTicketRequest
):
    """逐条校验回写前置条件。

    全部通过时返回 ``(ticket, replacement)`` 两个真实持久化实体；任何一条不满足
    则返回结构化失败结果。
    """
    # --- 1. 输入必须是已验证的 typed 请求 ---
    if not isinstance(request, UpdateTicketRequest):
        return _failure(
            UpdateTicketStatus.INVALID_REQUEST,
            "update_ticket 只接受已验证的 UpdateTicketRequest，不接受模型原始文本",
        )

    # --- 2. 回写必须由一次已持久化的 Run 执行 ---
    if not isinstance(agent_run, AgentRun) or agent_run.id is None:
        return _failure(
            UpdateTicketStatus.RUN_LINKAGE_INVALID,
            "update_ticket 必须由一次已持久化的 Agent Run 执行",
        )

    # --- 3. 工单必须真实存在 ---
    ticket = (
        db.query(Ticket).filter(Ticket.business_key == request.ticket_key).one_or_none()
    )
    if ticket is None:
        return _failure(
            UpdateTicketStatus.TICKET_NOT_FOUND,
            f"未找到工单: {request.ticket_key}",
        )

    # --- 4. Run 必须就是这张工单的 Run ---
    if agent_run.ticket_id != ticket.id:
        return _failure(
            UpdateTicketStatus.RUN_LINKAGE_INVALID,
            f"Agent Run {agent_run.business_key} 并不属于工单 {ticket.business_key}",
        )

    # --- 5. 引用的换货单必须在持久化状态中真实存在 ---
    # 这一条是"结果必须有实据"的落点：查不到就是查不到，绝不据文本宣称成功
    replacement = (
        db.query(ReplacementOrder)
        .filter(ReplacementOrder.business_key == request.replacement_key)
        .one_or_none()
    )
    if replacement is None:
        return _failure(
            UpdateTicketStatus.REPLACEMENT_NOT_FOUND,
            f"未找到换货单: {request.replacement_key}，无法据此回写工单",
        )

    # --- 6. 换货单必须是本工单、本次 Run 的真实成果 ---
    if replacement.ticket_id != ticket.id:
        return _failure(
            UpdateTicketStatus.REPLACEMENT_LINKAGE_INVALID,
            f"换货单 {replacement.business_key} 不属于工单 {ticket.business_key}",
        )
    if replacement.agent_run_id != agent_run.id:
        return _failure(
            UpdateTicketStatus.REPLACEMENT_LINKAGE_INVALID,
            f"换货单 {replacement.business_key} 不是 Agent Run "
            f"{agent_run.business_key} 执行的结果",
        )

    return ticket, replacement


def _finish_after_persistence_failure(
    db: Session,
    agent_run: AgentRun,
    request: UpdateTicketRequest,
    exc: SQLAlchemyError,
) -> UpdateTicketResult:
    """回滚之后，把持久化失败诚实地表示为结构化失败结果。

    回滚会使 ``agent_run`` 过期，因此重新按主键取回一个可用实例来记录步骤；
    取不回来时直接返回结果，绝不因为"记不下来"就把失败说成成功。
    """
    result = _failure(
        UpdateTicketStatus.PERSISTENCE_FAILED,
        f"工单 {request.ticket_key} 回写被持久化层拒绝: {type(exc).__name__}",
    )
    reloaded = db.query(AgentRun).filter(AgentRun.id == agent_run.id).one_or_none()
    if reloaded is None:
        return result
    return _finish(db, reloaded, result)


def _failure(status: UpdateTicketStatus, reason: str) -> UpdateTicketResult:
    """构造结构化失败结果。失败永远不携带工单状态。"""
    return UpdateTicketResult(status=status, failure_reason=reason)


def _record_view(ticket: Ticket, replacement: ReplacementOrder) -> TicketRecord:
    """把回写后的工单映射为类型化视图，结果引用用换货单的稳定业务标识表示。"""
    return TicketRecord(
        ticket_key=ticket.business_key,
        status=ticket.status,
        resolution=ticket.resolution,
        resolution_summary=ticket.resolution_summary,
        replacement_key=replacement.business_key,
        resolved_at=ticket.resolved_at,
    )


def _finish(
    db: Session, agent_run: AgentRun, result: UpdateTicketResult
) -> UpdateTicketResult:
    """把本次真实执行结果记录到 Agent Run 并提交事务。

    记录遵循 intent_service / inventory_service / replacement_service 的既有惯例：
    把 Run 从 queued 提升为 running，并在步骤上如实反映结果。区别在于本服务是
    Golden Path 的收尾：只有工单真的被回写，Run 才被置为 completed。
    """
    _mark_run_started(agent_run)
    step = _get_or_create_update_ticket_step(db, agent_run)
    step.completed_at = datetime.utcnow()

    if result.status is UpdateTicketStatus.UPDATED:
        step.status = AgentStepStatus.COMPLETED
        step.error_message = None
        _mark_run_completed(agent_run)
    else:
        step.status = AgentStepStatus.FAILED
        step.error_message = _format_failure_message(result)
        # Run 保持非 completed。这里不把 Run 判为 failed：与既有服务一致，
        # 单个 Tool 失败不等于整次 Run 终结，终态由端到端编排（T018）决定。

    db.commit()
    return result


def _mark_run_started(agent_run: AgentRun) -> None:
    """一次 Run 开始执行时，将 queued 提升为 running 并记录开始时间。"""
    if agent_run.status is AgentRunStatus.QUEUED:
        agent_run.status = AgentRunStatus.RUNNING
        agent_run.started_at = datetime.utcnow()


def _mark_run_completed(agent_run: AgentRun) -> None:
    """工单回写成功之后，才允许 Run 进入 completed。

    这是当前系统中唯一把 Run 置为 ``COMPLETED`` 的地方。Golden Path 的最后一个
    真实副作用就是工单回写，因此"Run 已完成"与"工单已被真实回写"是同一件事，
    不存在第二条能让 Run 显示完成的路径。
    """
    agent_run.status = AgentRunStatus.COMPLETED
    agent_run.completed_at = datetime.utcnow()
    agent_run.error_message = None


def _get_or_create_update_ticket_step(db: Session, agent_run: AgentRun) -> AgentStep:
    """取回该 Run 已有的回写步骤，或创建一条 running 起步的步骤记录。"""
    step = (
        db.query(AgentStep)
        .filter(
            AgentStep.agent_run_id == agent_run.id,
            AgentStep.name == UPDATE_TICKET_STEP_NAME,
        )
        .one_or_none()
    )
    if step is None:
        step = AgentStep(
            agent_run_id=agent_run.id,
            step_order=UPDATE_TICKET_STEP_ORDER,
            name=UPDATE_TICKET_STEP_NAME,
            status=AgentStepStatus.RUNNING,
        )
        db.add(step)
        db.flush()
    return step


def _format_failure_message(result: UpdateTicketResult) -> str:
    """把失败结果编码成可检查的结构化失败原因，写入 AgentStep.error_message。"""
    parts = [f"status={result.status.value}"]
    if result.failure_reason:
        parts.append(f"reason={result.failure_reason}")
    return "; ".join(parts)
