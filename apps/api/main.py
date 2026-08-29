from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from agent_run_service import (
    AgentRunNotFoundError,
    ApprovalNotFoundError,
    ResumeConflictError,
    resume_agent_run,
    run_golden_path,
)
from approval_decision_service import (
    ApprovalConflictError,
    ApprovalRequestNotFoundError,
    approve,
    reject,
)
from approval_service import approval_record, get_approval_request, get_pending_approval
from approvals import ApprovalRequestStatus
from config import settings
from database import get_db
from models import AgentRun, AgentRunStatus, Ticket
from risk_service import assess_persisted_replacement_risk


app = FastAPI(title=settings.app_name)

# 允许本地前端开发服务器（Vite 默认端口）跨域访问健康检查等接口。
# POST 是 T018 启动 Agent Run 所需：整条流程由一次真实的后端请求驱动。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    """健康检查响应模型"""
    status: str
    app_name: str
    environment: str


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """返回结构化的健康状态"""
    return HealthResponse(
        status="healthy",
        app_name=settings.app_name,
        environment=settings.app_env
    )


class TicketResponse(BaseModel):
    """工单只读响应模型"""
    business_key: str
    subject: str
    description: str
    status: str
    demo_scenario: str | None
    is_demo_data: bool
    created_at: datetime


@app.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(db: Session = Depends(get_db)) -> list[TicketResponse]:
    """返回已持久化的工单，按创建时间排序"""
    tickets = db.query(Ticket).order_by(Ticket.created_at.asc()).all()
    return [
        TicketResponse(
            business_key=ticket.business_key,
            subject=ticket.subject,
            description=ticket.description,
            status=ticket.status.value,
            demo_scenario=ticket.demo_scenario,
            is_demo_data=ticket.is_demo_data,
            created_at=ticket.created_at,
        )
        for ticket in tickets
    ]


class CustomerContextResponse(BaseModel):
    """工单关联的客户上下文"""
    business_key: str
    name: str
    email: str
    phone: str | None
    is_demo_data: bool


class OrderContextResponse(BaseModel):
    """工单关联的订单 / 商品上下文"""
    business_key: str
    product_sku: str
    product_name: str
    purchased_at: datetime
    status: str
    # 金额以字符串返回，避免浮点精度损失并保持展示与数据库一致
    amount: str
    is_demo_data: bool


class TicketDetailResponse(BaseModel):
    """工单详情只读响应模型，含关联的客户与订单上下文"""
    business_key: str
    subject: str
    description: str
    status: str
    demo_scenario: str | None
    is_demo_data: bool
    created_at: datetime
    customer: CustomerContextResponse | None
    order: OrderContextResponse | None


@app.get("/tickets/{business_key}", response_model=TicketDetailResponse)
async def get_ticket_detail(
    business_key: str, db: Session = Depends(get_db)
) -> TicketDetailResponse:
    """按业务标识返回单个已持久化工单及其关联的客户 / 订单上下文"""
    ticket = (
        db.query(Ticket)
        .options(joinedload(Ticket.customer), joinedload(Ticket.order))
        .filter(Ticket.business_key == business_key)
        .one_or_none()
    )
    # 未知业务标识返回诚实的 404，不提供任何回退或合成数据
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"未找到工单: {business_key}")

    customer = ticket.customer
    order = ticket.order

    return TicketDetailResponse(
        business_key=ticket.business_key,
        subject=ticket.subject,
        description=ticket.description,
        status=ticket.status.value,
        demo_scenario=ticket.demo_scenario,
        is_demo_data=ticket.is_demo_data,
        created_at=ticket.created_at,
        customer=CustomerContextResponse(
            business_key=customer.business_key,
            name=customer.name,
            email=customer.email,
            phone=customer.phone,
            is_demo_data=customer.is_demo_data,
        )
        if customer is not None
        else None,
        order=OrderContextResponse(
            business_key=order.business_key,
            product_sku=order.product_sku,
            product_name=order.product_name,
            purchased_at=order.purchased_at,
            status=order.status.value,
            amount=str(order.amount),
            is_demo_data=order.is_demo_data,
        )
        if order is not None
        else None,
    )


class AgentStepResponse(BaseModel):
    """Agent Run 时间线上的一个真实步骤。

    步骤只有在对应服务真实返回之后才会被写入，因此这里出现的每一条都代表一次
    已经发生的执行；``error_message`` 是那次执行的结构化失败原因。
    """
    step_order: int
    name: str
    status: str
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None


class AgentRunReplacementResponse(BaseModel):
    """本次 Run 真实创建并落库的换货单"""
    business_key: str
    status: str
    product_sku: str
    reason: str
    is_demo_data: bool
    created_at: datetime


class AgentRunTicketResultResponse(BaseModel):
    """执行之后工单的真实持久化状态。

    未被回写的工单，``resolution`` 等字段保持为 null —— 不用占位值伪造解决事实。
    """
    status: str
    resolution: str | None
    resolution_summary: str | None
    resolved_at: datetime | None
    replacement_key: str | None


class AgentRunRiskResponse(BaseModel):
    """T019 风险门禁给出的结构化判断，直接供 UI 展示。

    前端不重新推导风险规则，只消费这里的级别、规则标识与原因文本。金额以字符串
    返回，避免浮点精度损失并保持展示与数据库一致。
    """
    action: str
    level: str
    rule_code: str
    requires_approval: bool
    reason: str
    order_key: str | None
    order_amount: str | None
    approval_threshold_amount: str | None
    policy_key: str | None


class AgentRunApprovalRequestResponse(BaseModel):
    """等待人工审批时那条真实落库的审批请求（T020）。

    这是"为什么停在这里"的权威来源：``risk`` 直接来自审批请求创建时保存的
    快照，因此 UI 不需要、也不应该再去重算当前政策来还原历史原因。售后政策
    阈值日后被改动，这里展示的拦截原因也不会跟着变。

    只暴露稳定的业务标识与快照内容，不含自增主键或任何 ORM 内部字段。
    """
    approval_key: str
    status: str
    protected_action: str
    created_at: datetime
    # pending 审批尚未有结果，因此该字段恒为 null，不用占位时间伪造审批事实
    resolved_at: datetime | None
    # 风险规则触发那一刻的快照，不是此刻重算的结论
    risk: AgentRunRiskResponse


class ApprovalDecisionRequest(BaseModel):
    """人工审批决策的可选请求体（T021）。

    ``decision_reason`` 为空表示只做决策、不附带理由。它只在首次决策时被落库，
    同决策重试不会覆盖既有理由。
    """

    decision_reason: str | None = None


class ApprovalDecisionResponse(BaseModel):
    """人工审批决策之后那条审批请求的真实持久化状态（T021）。

    ``risk`` 仍是触发时刻保存的 snapshot，不是此刻重算的结论；``agent_run_status``
    说明所属 Run 现在停在哪里 —— approve 后仍是 ``waiting_for_approval``（恢复
    执行是 T022 的事），reject 后进入 ``cancelled`` 终止态。
    """

    approval_key: str
    status: str
    protected_action: str
    agent_run_key: str
    agent_run_status: str
    resolved_at: datetime | None
    decision_reason: str | None
    risk: AgentRunRiskResponse


class AgentRunResponse(BaseModel):
    """一次 Agent Run 的真实执行结果。

    全部字段都在执行结束后从持久化状态读出：``status`` 为 ``completed`` 当且仅当
    工单已被真实回写，任何 Tool 失败都会体现为 ``failed`` 与结构化的
    ``error_message``，且失败之后的步骤根本不会出现在 ``steps`` 中。风险门禁命中
    时 ``status`` 为 ``waiting_for_approval``，且 ``risk`` 携带后端给出的可展示
    原因。
    """
    business_key: str
    ticket_key: str
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    steps: list[AgentStepResponse]
    replacement: AgentRunReplacementResponse | None
    ticket_result: AgentRunTicketResultResponse
    risk: AgentRunRiskResponse | None
    # T020：等待人工审批时那条真实落库的审批请求。它是审批语义的权威来源；
    # 未被风险门禁拦下的 Run 没有审批请求，此处为 null。
    approval_request: AgentRunApprovalRequestResponse | None


class ResumeAgentRunResponse(BaseModel):
    """T022 恢复执行的响应：本次 Run 的真实终态 + 授权它的那条审批请求。

    审批请求字段（key / status / protected_action）来自授权本次执行的那条 durable
    ApprovalRequest；Run 字段（含 replacement 身份、ticket 当前状态与步骤时间线）
    来自恢复执行后的真实持久化状态。低风险 Run 没有审批请求，``approval`` 为 null。
    """

    agent_run: AgentRunResponse
    approval: ApprovalDecisionResponse | None = None


def _risk_view(risk) -> AgentRunRiskResponse:
    """把一次风险判断（当场求值的结果或持久化快照）映射为响应模型。

    ``RiskAssessment`` 与 ``RiskSnapshot`` 的字段一一对应，因此两者共用同一个
    映射，UI 拿到的结构始终一致。金额以字符串返回，避免浮点精度损失。
    """
    return AgentRunRiskResponse(
        action=risk.action.value,
        level=risk.level.value,
        rule_code=risk.rule_code.value,
        requires_approval=risk.requires_approval,
        reason=risk.reason,
        order_key=risk.order_key,
        order_amount=str(risk.order_amount) if risk.order_amount is not None else None,
        approval_threshold_amount=(
            str(risk.approval_threshold_amount)
            if risk.approval_threshold_amount is not None
            else None
        ),
        policy_key=risk.policy_key,
    )


def _approval_request_response(
    db: Session, agent_run: AgentRun
) -> AgentRunApprovalRequestResponse | None:
    """取回该 Run 待处理的审批请求，并以其持久化快照作答。

    ``approval_record`` 只读取落库的快照列，不查询当前 Order / AfterSalesPolicy，
    也不调用任何风险规则求值 —— 展示的是触发时刻的历史事实。
    """
    approval = get_pending_approval(db, agent_run)
    if approval is None:
        return None
    record = approval_record(approval)
    return AgentRunApprovalRequestResponse(
        approval_key=record.approval_key,
        status=record.status.value,
        protected_action=record.protected_action.value,
        created_at=record.created_at,
        resolved_at=record.resolved_at,
        risk=_risk_view(record.risk),
    )


def _risk_response(
    db: Session,
    agent_run: AgentRun,
    approval: AgentRunApprovalRequestResponse | None,
) -> AgentRunRiskResponse | None:
    """把风险原因交给 UI，优先使用审批请求保存的历史快照。

    数据源有明确优先级：只要存在待审批请求，就以它创建时的快照为准，绝不用
    当前政策的重算结果冒充历史事实。只有在 Run 停在等待审批却查不到审批请求
    这种历史遗留情形下，才退回按持久化状态重新求值 —— 那时如实展示的是"此刻
    重算的结论"，而不是伪造的当时依据。

    只在 Run 停在等待审批时返回：这是风险门禁真正拦下动作的场景。其余状态的
    Run 不携带 ``risk``，避免把"没有拦下"误读成一次风险判断。
    """
    if agent_run.status is not AgentRunStatus.WAITING_FOR_APPROVAL:
        return None
    if approval is not None:
        return approval.risk
    risk = assess_persisted_replacement_risk(db, agent_run)
    if risk is None:
        return None
    return _risk_view(risk)


def _agent_run_response(db: Session, agent_run: AgentRun) -> AgentRunResponse:
    """把已持久化的 Run 映射为响应模型，不做任何状态推断或补齐"""
    ticket = agent_run.ticket
    replacement = agent_run.replacement
    resolution_replacement = ticket.resolution_replacement
    approval_request = _approval_request_response(db, agent_run)

    return AgentRunResponse(
        business_key=agent_run.business_key,
        ticket_key=ticket.business_key,
        status=agent_run.status.value,
        created_at=agent_run.created_at,
        started_at=agent_run.started_at,
        completed_at=agent_run.completed_at,
        error_message=agent_run.error_message,
        approval_request=approval_request,
        risk=_risk_response(db, agent_run, approval_request),
        # 关系上已按 step_order 排序，时间线顺序即真实执行顺序
        steps=[
            AgentStepResponse(
                step_order=step.step_order,
                name=step.name,
                status=step.status.value,
                started_at=step.started_at,
                completed_at=step.completed_at,
                error_message=step.error_message,
            )
            for step in agent_run.steps
        ],
        replacement=AgentRunReplacementResponse(
            business_key=replacement.business_key,
            status=replacement.status.value,
            product_sku=replacement.product_sku,
            reason=replacement.reason,
            is_demo_data=replacement.is_demo_data,
            created_at=replacement.created_at,
        )
        if replacement is not None
        else None,
        ticket_result=AgentRunTicketResultResponse(
            status=ticket.status.value,
            resolution=ticket.resolution.value if ticket.resolution is not None else None,
            resolution_summary=ticket.resolution_summary,
            resolved_at=ticket.resolved_at,
            replacement_key=(
                resolution_replacement.business_key
                if resolution_replacement is not None
                else None
            ),
        ),
    )


def _require_ticket(db: Session, business_key: str) -> Ticket:
    """取回工单，不存在时返回诚实的 404"""
    ticket = (
        db.query(Ticket)
        .options(joinedload(Ticket.order))
        .filter(Ticket.business_key == business_key)
        .one_or_none()
    )
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"未找到工单: {business_key}")
    return ticket


@app.post(
    "/tickets/{business_key}/agent-runs",
    response_model=AgentRunResponse,
    status_code=201,
)
async def start_agent_run(
    business_key: str, db: Session = Depends(get_db)
) -> AgentRunResponse:
    """启动并同步执行一次完整的 Agent Run。

    这是 T018 的单次启动入口：一次请求真实驱动 intent → 政策 → 订单 → 库存 →
    判定 → 换货 → 工单回写整条流程。HTTP 201 只表示"这次 Run 已被创建并执行
    完毕"，执行成功与否一律以响应体中的 ``status`` 为准 —— Tool 失败时它是
    ``failed``，绝不会因为请求本身完成就显示为成功。
    """
    ticket = _require_ticket(db, business_key)
    agent_run = run_golden_path(db, ticket)
    return _agent_run_response(db, agent_run)


@app.get(
    "/tickets/{business_key}/agent-runs/latest",
    response_model=AgentRunResponse,
)
async def get_latest_agent_run(
    business_key: str, db: Session = Depends(get_db)
) -> AgentRunResponse:
    """返回该工单最近一次真实执行的 Agent Run。

    工单从未执行过 Run 时返回 404，而不是一个空壳 Run：没有执行就是没有执行。
    """
    ticket = _require_ticket(db, business_key)
    agent_run = (
        db.query(AgentRun)
        .filter(AgentRun.ticket_id == ticket.id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .first()
    )
    if agent_run is None:
        raise HTTPException(
            status_code=404, detail=f"工单 {business_key} 尚未执行过 Agent Run"
        )
    return _agent_run_response(db, agent_run)


def _decision_reason(body: ApprovalDecisionRequest | None) -> str | None:
    """取回可选决策理由；没有请求体等同于没有理由。"""
    return body.decision_reason if body is not None else None


def _approval_decision_response(
    db: Session,
    approval_key: str,
    target: ApprovalRequestStatus,
    decision_reason: str | None,
) -> ApprovalDecisionResponse:
    """执行一次一次性审批决策，并把它映射为真实持久化状态的响应。

    404 / 409 直接在这里转换为 HTTP 语义：不存在是 404，相反决策是 409。决策
    成功的 200 响应一律来自数据库里那条真实记录，而不是决策函数的返回值本身。
    """
    try:
        if target is ApprovalRequestStatus.APPROVED:
            approval = approve(db, approval_key, decision_reason)
        else:
            approval = reject(db, approval_key, decision_reason)
    except ApprovalRequestNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ApprovalConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return _approval_decision_view(approval_record(approval))


def _approval_decision_view(record) -> ApprovalDecisionResponse:
    """把已持久化审批请求的类型化视图映射为决策响应模型。

    与 T021 的决策端点共用同一映射：只暴露稳定业务标识、状态与快照，不含自增
    主键或任何 ORM 内部字段。T022 的 Resume 响应同样用它展示授权来源。
    """
    return ApprovalDecisionResponse(
        approval_key=record.approval_key,
        status=record.status.value,
        protected_action=record.protected_action.value,
        agent_run_key=record.agent_run_key,
        agent_run_status=record.agent_run_status,
        resolved_at=record.resolved_at,
        decision_reason=record.decision_reason,
        risk=_risk_view(record.risk),
    )


@app.post(
    "/approval-requests/{approval_key}/approve",
    response_model=ApprovalDecisionResponse,
)
async def approve_approval_request(
    approval_key: str,
    body: ApprovalDecisionRequest | None = None,
    db: Session = Depends(get_db),
) -> ApprovalDecisionResponse:
    """批准一条 pending 审批请求（T021）。

    只把审批请求记录为 APPROVED，不执行受保护动作、不更新工单、不恢复 Run，
    也不把 Run 标 COMPLETED —— 恢复执行是 T022 的事。
    """
    return _approval_decision_response(
        db, approval_key, ApprovalRequestStatus.APPROVED, _decision_reason(body)
    )


@app.post(
    "/approval-requests/{approval_key}/reject",
    response_model=ApprovalDecisionResponse,
)
async def reject_approval_request(
    approval_key: str,
    body: ApprovalDecisionRequest | None = None,
    db: Session = Depends(get_db),
) -> ApprovalDecisionResponse:
    """拒绝一条 pending 审批请求（T021）。

    把审批请求记录为 REJECTED，并让仍停在等待审批的 Run 转入 CANCELLED 终止态；
    不产生换货单、不更新工单。
    """
    return _approval_decision_response(
        db, approval_key, ApprovalRequestStatus.REJECTED, _decision_reason(body)
    )


@app.post(
    "/agent-runs/{agent_run_key}/resume",
    response_model=ResumeAgentRunResponse,
)
async def resume_agent_run_endpoint(
    agent_run_key: str, db: Session = Depends(get_db)
) -> ResumeAgentRunResponse:
    """对一条已 APPROVED 的 Run 显式恢复执行（T022）。

    这是 approve 之后的**独立**动作：只依据数据库里那条 durable ApprovalRequest
    判定是否允许恢复，绝不接受任何客户端夹带的 approved / skipRisk / bypass 字段。
    不存在 Run 或审批请求返回 404；pending / rejected / 不匹配 / 状态不允许一律
    409；已经 completed 的 Run 返回当前状态（幂等），不重新执行业务动作。
    """
    try:
        agent_run = resume_agent_run(db, agent_run_key)
    except (AgentRunNotFoundError, ApprovalNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ResumeConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    approval = get_approval_request(db, agent_run)
    return ResumeAgentRunResponse(
        agent_run=_agent_run_response(db, agent_run),
        approval=(
            _approval_decision_view(approval_record(approval))
            if approval is not None
            else None
        ),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
