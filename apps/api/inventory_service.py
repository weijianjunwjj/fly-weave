"""check_inventory Tool 的 application / data query boundary 与 Agent Run 记录。

Golden Path 的库存查询 Tool：把一个已验证的 typed 请求解析为对持久化
``InventoryItem`` 记录的精确查询，再把数据库中真实存在的事实包装成 typed
结果；无货、查无 SKU 与非法输入都各自返回结构化的不同结果，绝不编造可用性。

    Agent / workflow
        → CheckInventoryRequest（已验证）
        → 本服务的仓储查询
        → 持久化的 InventoryItem
        → InventoryCheckResult

``check_inventory`` 只负责查询边界，与 get_order 完全一致。``check_and_persist_inventory``
在查询之后，把真实的 Tool 调用与其实际返回结果记录到给定的 AgentRun / AgentStep，
以及类型化的 ``AgentInventoryCheck`` 表；记录的内容只来自本次查询的真实结果，
绝不预先构造或合成。
"""
from datetime import datetime

from sqlalchemy.orm import Session

from audit_service import record_audit_event
from inventory import (
    CheckInventoryRequest,
    InventoryCheckResult,
    InventoryCheckStatus,
    InventoryFacts,
)
from models import (
    ActorType,
    AgentInventoryCheck,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    AuditEventType,
    InventoryItem,
)

# check_inventory 在 Golden Path 中的步骤序号。与 intent 步骤（step_order=1）
# 不同、互不冲突，并保留后续 policy / order 步骤的占位空间。
INVENTORY_CHECK_STEP_NAME = "检查换货库存"
INVENTORY_CHECK_STEP_ORDER = 4


def check_inventory(db: Session, request: CheckInventoryRequest) -> InventoryCheckResult:
    """按 SKU 查询真实持久化库存。

    输入必须是 ``CheckInventoryRequest``；任何非结构化输入（例如模型的原始输出
    字符串）都在 boundary 处被拒绝，返回 ``INVALID_REQUEST``，绝不进入查询与
    成功路径。成功与否完全由数据库查询结果决定。
    """
    if not isinstance(request, CheckInventoryRequest):
        return InventoryCheckResult(
            status=InventoryCheckStatus.INVALID_REQUEST,
            requested_sku=None,
            failure_reason=(
                "check_inventory 只接受已验证的 CheckInventoryRequest，"
                "不接受模型原始文本"
            ),
        )

    item = (
        db.query(InventoryItem)
        .filter(InventoryItem.product_sku == request.product_sku)
        .one_or_none()
    )
    if item is None:
        return InventoryCheckResult(
            status=InventoryCheckStatus.SKU_NOT_FOUND,
            requested_sku=request.product_sku,
            failure_reason=f"未找到库存: {request.product_sku}",
        )

    facts = InventoryFacts(
        product_sku=item.product_sku,
        product_name=item.product_name,
        available_quantity=item.available_quantity,
        warehouse=item.warehouse,
        is_demo_data=item.is_demo_data,
    )
    # 有货 / 无货都来自数据库真实值：available_quantity == 0 显式表示为
    # UNAVAILABLE，而不是被省略、被替换或被当作失败
    status = (
        InventoryCheckStatus.SUCCESS
        if item.available_quantity > 0
        else InventoryCheckStatus.UNAVAILABLE
    )
    return InventoryCheckResult(
        status=status,
        requested_sku=request.product_sku,
        inventory=facts,
    )


def check_and_persist_inventory(
    db: Session, agent_run: AgentRun, request: CheckInventoryRequest
) -> InventoryCheckResult:
    """执行 check_inventory，并把真实调用与结果记录到给定 AgentRun。

    结果记录遵循 intent_service 的记录惯例：把 Run 从 queued 提升为 running、
    在 AgentStep 中记录步骤状态（有货/无货为 completed，其余失败为 failed），
    并把实际结果以类型化字段写入 AgentInventoryCheck（每 Run 至多一条）。
    """
    result = check_inventory(db, request)
    _mark_run_started(agent_run)
    step = _get_or_create_inventory_step(db, agent_run)
    now = datetime.utcnow()

    if result.status in (
        InventoryCheckStatus.SUCCESS,
        InventoryCheckStatus.UNAVAILABLE,
    ):
        step.status = AgentStepStatus.COMPLETED
        step.completed_at = now
        step.error_message = None
    else:
        step.status = AgentStepStatus.FAILED
        step.completed_at = now
        step.error_message = _format_failure_message(result)

    _upsert_agent_inventory_check(db, agent_run, result)
    # T023：check_inventory 已真实执行，结果直接来自本次查询。有货 / 无货 / 查无 /
    # 非法输入四种状态各自如实记录，success 只在真正查到可用库存时为真。
    requested_sku = (
        result.requested_sku
        if result.requested_sku is not None
        else getattr(request, "product_sku", None)
    )
    record_audit_event(
        db,
        agent_run=agent_run,
        event_type=AuditEventType.CHECK_INVENTORY,
        actor_type=ActorType.AGENT,
        outcome=result.status.value,
        success=result.status is InventoryCheckStatus.SUCCESS,
        action="check_inventory",
        summary=f"检查库存: sku={requested_sku} status={result.status.value}",
        affected_object_type="inventory_item",
        affected_object_key=requested_sku,
    )
    db.commit()
    return result


def _mark_run_started(agent_run: AgentRun) -> None:
    """一次 Run 开始执行时，将 queued 提升为 running 并记录开始时间。

    与 intent_service 保持一致：只负责"已经开始"这一事实，Run 最终是否
    completed / failed 由后续端到端编排决定。
    """
    if agent_run.status is AgentRunStatus.QUEUED:
        agent_run.status = AgentRunStatus.RUNNING
        agent_run.started_at = datetime.utcnow()


def _get_or_create_inventory_step(db: Session, agent_run: AgentRun) -> AgentStep:
    """取回该 Run 已有的 inventory 步骤，或创建一条 running 起步的步骤记录。"""
    step = (
        db.query(AgentStep)
        .filter(
            AgentStep.agent_run_id == agent_run.id,
            AgentStep.name == INVENTORY_CHECK_STEP_NAME,
        )
        .one_or_none()
    )
    if step is None:
        step = AgentStep(
            agent_run_id=agent_run.id,
            step_order=INVENTORY_CHECK_STEP_ORDER,
            name=INVENTORY_CHECK_STEP_NAME,
            status=AgentStepStatus.RUNNING,
        )
        db.add(step)
        db.flush()
    return step


def _upsert_agent_inventory_check(
    db: Session, agent_run: AgentRun, result: InventoryCheckResult
) -> AgentInventoryCheck:
    """把真实库存查询结果以类型化字段写入 AgentInventoryCheck（每 Run 至多一条）。

    只写入本次查询真实返回的事实：有货时写可用数量与仓库，无货时写
    available_quantity=0 的显式无货事实，查无/非法输入时写结构化失败原因，
    绝不预先构造成功或可用性。
    """
    persisted = (
        db.query(AgentInventoryCheck)
        .filter(AgentInventoryCheck.agent_run_id == agent_run.id)
        .one_or_none()
    )
    if persisted is None:
        persisted = AgentInventoryCheck(agent_run_id=agent_run.id)
        db.add(persisted)

    persisted.requested_sku = result.requested_sku
    persisted.status = result.status.value
    persisted.failure_reason = result.failure_reason

    if result.inventory is not None:
        persisted.available_quantity = result.inventory.available_quantity
        persisted.warehouse = result.inventory.warehouse
        persisted.is_demo_data = result.inventory.is_demo_data
    else:
        persisted.available_quantity = None
        persisted.warehouse = None
        persisted.is_demo_data = None
    return persisted


def _format_failure_message(result: InventoryCheckResult) -> str:
    """把失败结果编码成可检查的结构化失败原因，写入 AgentStep.error_message。"""
    parts = [f"status={result.status.value}"]
    if result.failure_reason:
        parts.append(f"reason={result.failure_reason}")
    return "; ".join(parts)
