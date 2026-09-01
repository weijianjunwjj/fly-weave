import json
from datetime import datetime
from decimal import Decimal
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
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
from approval_service import approval_record, get_approval_request
from approvals import ApprovalRequestStatus
from config import settings
from database import get_db
from models import AgentRun, AgentRunStatus, ApprovalRequest, AuditEvent, Ticket
from risk_service import assess_persisted_replacement_risk
from ticket_intake_service import (
    DuplicateOrderError,
    IntakeProductUnavailableError,
    create_ticket,
)


app = FastAPI(title=settings.app_name)

# 允许本地前端开发服务器（Vite 默认端口）跨域访问健康检查等接口。
# POST 是 T018 启动 Agent Run 所需：整条流程由一次真实的后端请求驱动。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
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
    """Service Operations 工单列表使用的真实聚合视图。"""
    business_key: str
    subject: str
    issue_type: str | None
    description: str
    status: str
    demo_scenario: str | None
    is_demo_data: bool
    created_at: datetime
    updated_at: datetime
    customer_name: str | None
    order_key: str | None
    order_amount: str | None
    agent_run_key: str | None
    agent_run_status: str | None
    risk_level: str | None


@app.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(db: Session = Depends(get_db)) -> list[TicketResponse]:
    """返回产品工作台所需的持久化工单、最新 Run 与真实风险状态。"""
    tickets = (
        db.query(Ticket)
        .options(
            joinedload(Ticket.customer),
            joinedload(Ticket.order),
            joinedload(Ticket.agent_runs),
        )
        .order_by(Ticket.updated_at.desc(), Ticket.id.desc())
        .all()
    )
    responses: list[TicketResponse] = []
    for ticket in tickets:
        latest_run = (
            max(ticket.agent_runs, key=lambda run: run.id)
            if ticket.agent_runs
            else None
        )
        risk = None
        if latest_run is not None:
            pending = _approval_request_response(db, latest_run)
            risk = _risk_response(db, latest_run, pending)
        responses.append(
            TicketResponse(
                business_key=ticket.business_key,
                subject=ticket.subject,
                issue_type=ticket.issue_type,
                description=ticket.description,
                status=ticket.status.value,
                demo_scenario=ticket.demo_scenario,
                is_demo_data=ticket.is_demo_data,
                created_at=ticket.created_at,
                updated_at=ticket.updated_at,
                customer_name=ticket.customer.name if ticket.customer is not None else None,
                order_key=ticket.order.business_key if ticket.order is not None else None,
                order_amount=str(ticket.order.amount) if ticket.order is not None else None,
                agent_run_key=latest_run.business_key if latest_run is not None else None,
                agent_run_status=latest_run.status.value if latest_run is not None else None,
                risk_level=risk.level if risk is not None else None,
            )
        )
    return responses


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
    issue_type: str | None
    description: str
    status: str
    demo_scenario: str | None
    is_demo_data: bool
    created_at: datetime
    updated_at: datetime
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
        issue_type=ticket.issue_type,
        description=ticket.description,
        status=ticket.status.value,
        demo_scenario=ticket.demo_scenario,
        is_demo_data=ticket.is_demo_data,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
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


class CreateTicketRequest(BaseModel):
    """正式工单受理输入；字段只映射现有 Customer、Order 与 Ticket。"""

    customer_name: str = Field(min_length=1, max_length=128)
    customer_email: str = Field(min_length=3, max_length=255)
    issue_type: Literal["商品损坏", "换货"]
    issue_description: str = Field(min_length=1, max_length=4000)
    order_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    )
    order_amount: Decimal = Field(gt=0, max_digits=10, decimal_places=2)

    @field_validator("customer_name", "issue_description", "order_id")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段不得为空白")
        return value.strip()

    @field_validator("customer_email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        normalized = value.strip()
        if "@" not in normalized or " " in normalized:
            raise ValueError("请输入有效邮箱")
        return normalized


@app.post("/tickets", response_model=TicketDetailResponse, status_code=201)
async def create_ticket_endpoint(
    body: CreateTicketRequest, db: Session = Depends(get_db)
) -> TicketDetailResponse:
    """通过真实事务创建 Customer、Order 与 Ticket，刷新后仍可查询。"""

    try:
        ticket = create_ticket(
            db,
            customer_name=body.customer_name,
            customer_email=body.customer_email,
            issue_type=body.issue_type,
            issue_description=body.issue_description,
            order_key=body.order_id,
            order_amount=body.order_amount,
        )
    except DuplicateOrderError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except IntakeProductUnavailableError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    customer = ticket.customer
    order = ticket.order
    return TicketDetailResponse(
        business_key=ticket.business_key,
        subject=ticket.subject,
        issue_type=ticket.issue_type,
        description=ticket.description,
        status=ticket.status.value,
        demo_scenario=ticket.demo_scenario,
        is_demo_data=ticket.is_demo_data,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        customer=CustomerContextResponse(
            business_key=customer.business_key,
            name=customer.name,
            email=customer.email,
            phone=customer.phone,
            is_demo_data=customer.is_demo_data,
        ),
        order=OrderContextResponse(
            business_key=order.business_key,
            product_sku=order.product_sku,
            product_name=order.product_name,
            purchased_at=order.purchased_at,
            status=order.status.value,
            amount=str(order.amount),
            is_demo_data=order.is_demo_data,
        ),
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
    """本次 Run 对应的真实落库审批请求（T020/T021）。

    这是"为什么停在这里"的权威来源：``risk`` 直接来自审批请求创建时保存的
    快照，因此 UI 不需要、也不应该再去重算当前政策来还原历史原因。售后政策
    阈值日后被改动，这里展示的拦截原因也不会跟着变。

    只暴露稳定的业务标识与快照内容，不含自增主键或任何 ORM 内部字段。
    """
    approval_key: str
    status: str
    protected_action: str
    created_at: datetime
    # pending 时为空；人工决策后返回真实 resolved_at。
    resolved_at: datetime | None
    # 风险规则触发那一刻的快照，不是此刻重算的结论
    risk: AgentRunRiskResponse


class AgentRunCenterApprovalRequestResponse(AgentRunApprovalRequestResponse):
    """Execution Center 专用审批视图，扩展人工决策理由。

    旧 AgentRun API 继续使用 AgentRunApprovalRequestResponse，避免改变既有
    start/latest payload；只有新的聚合读取接口暴露该字段。
    """

    decision_reason: str | None


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


class PolicyBasisPassageResponse(BaseModel):
    """T025 policy retrieval 返回的一条真实 passage。

    每个字段都来自真实 retrieval result（rank / score / chunk_key / chunk_order /
    passage），前端原样展示，绝不自行生成 citation 或来源身份。
    """

    rank: int
    score: float
    chunk_key: str
    chunk_order: int
    passage: str


class PolicyBasisResponse(BaseModel):
    """T025 政策依据：一次真实 policy retrieval 的来源与 selected passages。

    ``document_title`` / ``document_key`` / ``source_reference`` 是检索命中的
    PolicyDocument 稳定身份，``passages`` 是被选中 passage 的安全快照。retrieval
    失败时来源字段为 null 且 ``failure_reason`` 非空，前端据此如实展示失败，绝不
    显示"AI 判断符合政策"这类没有来源的结论。
    """

    status: str
    query_summary: str
    document_key: str | None
    document_title: str | None
    source_reference: str | None
    is_demo_data: bool | None
    failure_reason: str | None
    passages: list[PolicyBasisPassageResponse]


class AgentRunRecommendationResponse(BaseModel):
    """已验证并持久化的 AI 意图，作为面向运营人员的建议摘要。"""

    action: str
    issue_summary: str
    confidence: float


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
    recommendation: AgentRunRecommendationResponse | None
    risk: AgentRunRiskResponse | None
    # T020：等待人工审批时那条真实落库的审批请求。它是审批语义的权威来源；
    # 未被风险门禁拦下的 Run 没有审批请求，此处为 null。
    approval_request: AgentRunApprovalRequestResponse | None
    # T025：policy retrieval 的真实来源与 selected passages。策略检索是 Golden Path
    # 的固定步骤，因此正常执行的 Run 都会携带它；仅在极旧历史数据缺失时为 null。
    policy_basis: PolicyBasisResponse | None = None


class AgentRunCenterRunResponse(AgentRunResponse):
    """Execution Center 专用 Run DTO，审批快照包含真实人工决策理由。"""

    approval_request: AgentRunCenterApprovalRequestResponse | None


class ApprovalInboxItemResponse(BaseModel):
    """审批工作台的一条真实聚合视图。

    审批、Run、Ticket 与 Policy Basis 均直接映射既有持久化实体；本响应只为
    Approval Inbox 减少前端 N+1 请求，不创建第二套审批状态或执行生命周期。
    """

    approval: ApprovalDecisionResponse
    created_at: datetime
    ticket: TicketDetailResponse
    agent_run: AgentRunResponse


class AgentRunCenterItemResponse(BaseModel):
    """AI Execution Center 的真实聚合读取视图。

    Run、Ticket 与 Approval 都直接映射已有持久化实体；该模型只减少前端
    N+1 请求，不创建新的执行状态、步骤或业务结论。
    """

    agent_run: AgentRunCenterRunResponse
    ticket: TicketDetailResponse


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
    db: Session,
    agent_run: AgentRun,
    *,
    include_decision_reason: bool = False,
) -> AgentRunApprovalRequestResponse | AgentRunCenterApprovalRequestResponse | None:
    """取回该 Run 的审批请求，并以其持久化快照作答。

    ``approval_record`` 只读取落库的快照列，不查询当前 Order / AfterSalesPolicy，
    也不调用任何风险规则求值 —— 展示的是触发时刻的历史事实。
    """
    approval = get_approval_request(db, agent_run)
    if approval is None:
        return None
    record = approval_record(approval)
    common = dict(
        approval_key=record.approval_key,
        status=record.status.value,
        protected_action=record.protected_action.value,
        created_at=record.created_at,
        resolved_at=record.resolved_at,
        risk=_risk_view(record.risk),
    )
    if include_decision_reason:
        return AgentRunCenterApprovalRequestResponse(
            **common,
            decision_reason=record.decision_reason,
        )
    return AgentRunApprovalRequestResponse(**common)


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

    已存在审批请求时始终返回其触发时刻的风险快照，使已批准或已拒绝记录仍可
    解释；没有审批历史时，仅等待审批的遗留 Run 才允许按持久化状态重算。
    """
    if approval is not None:
        return approval.risk
    if agent_run.status is not AgentRunStatus.WAITING_FOR_APPROVAL:
        return None
    risk = assess_persisted_replacement_risk(db, agent_run)
    if risk is None:
        return None
    return _risk_view(risk)


def _policy_basis_response(agent_run: AgentRun) -> PolicyBasisResponse | None:
    """把持久化的 policy retrieval 记录映射为政策依据响应。

    只读取真实落库的 ``AgentPolicyRetrieval``（含 selected passages 快照），不重新
    检索、不调用模型、不补齐任何来源身份。历史数据没有检索记录时返回 None。
    """
    retrieval = agent_run.policy_retrieval
    if retrieval is None:
        return None

    passages: list[PolicyBasisPassageResponse] = []
    if retrieval.passages_json:
        try:
            raw = json.loads(retrieval.passages_json)
        except (ValueError, TypeError):
            raw = []
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                passages.append(
                    PolicyBasisPassageResponse(
                        rank=item.get("rank", 0),
                        score=item.get("score", 0.0),
                        chunk_key=item.get("chunk_key", ""),
                        chunk_order=item.get("chunk_order", 0),
                        passage=item.get("passage", ""),
                    )
                )

    return PolicyBasisResponse(
        status=retrieval.status,
        query_summary=retrieval.query_summary,
        document_key=retrieval.document_key,
        document_title=retrieval.document_title,
        source_reference=retrieval.source_reference,
        is_demo_data=retrieval.is_demo_data,
        failure_reason=retrieval.failure_reason,
        passages=passages,
    )


def _agent_run_response(
    db: Session,
    agent_run: AgentRun,
    *,
    include_decision_reason: bool = False,
) -> AgentRunResponse | AgentRunCenterRunResponse:
    """映射持久化 Run；仅 Execution Center 扩展人工决策理由。"""
    ticket = agent_run.ticket
    replacement = agent_run.replacement
    resolution_replacement = ticket.resolution_replacement
    approval_request = _approval_request_response(
        db,
        agent_run,
        include_decision_reason=include_decision_reason,
    )
    response_type = (
        AgentRunCenterRunResponse if include_decision_reason else AgentRunResponse
    )

    return response_type(
        business_key=agent_run.business_key,
        ticket_key=ticket.business_key,
        status=agent_run.status.value,
        created_at=agent_run.created_at,
        started_at=agent_run.started_at,
        completed_at=agent_run.completed_at,
        error_message=agent_run.error_message,
        approval_request=approval_request,
        recommendation=(
            AgentRunRecommendationResponse(
                action=agent_run.intent.requested_action,
                issue_summary=agent_run.intent.issue_summary,
                confidence=agent_run.intent.confidence,
            )
            if agent_run.intent is not None
            else None
        ),
        risk=_risk_response(db, agent_run, approval_request),
        policy_basis=_policy_basis_response(agent_run),
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


@app.get(
    "/approval-requests",
    response_model=list[ApprovalInboxItemResponse],
)
async def list_approval_requests(
    db: Session = Depends(get_db),
) -> list[ApprovalInboxItemResponse]:
    """返回 Approval Inbox 所需的真实审批记录，包含已决策历史。

    排序以审批创建时间和数据库 id 倒序确定。状态、风险快照、Run 结果、工单结果
    与政策依据都来自既有持久化模型；这里不重算风险，也不补造执行或审计事实。
    """

    approvals = (
        db.query(ApprovalRequest)
        .options(
            joinedload(ApprovalRequest.agent_run)
            .joinedload(AgentRun.ticket)
            .joinedload(Ticket.customer),
            joinedload(ApprovalRequest.agent_run)
            .joinedload(AgentRun.ticket)
            .joinedload(Ticket.order),
        )
        .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
        .all()
    )

    items: list[ApprovalInboxItemResponse] = []
    for approval in approvals:
        run = approval.agent_run
        ticket = run.ticket
        customer = ticket.customer
        order = ticket.order
        items.append(
            ApprovalInboxItemResponse(
                approval=_approval_decision_view(approval_record(approval)),
                created_at=approval.created_at,
                ticket=TicketDetailResponse(
                    business_key=ticket.business_key,
                    subject=ticket.subject,
                    issue_type=ticket.issue_type,
                    description=ticket.description,
                    status=ticket.status.value,
                    demo_scenario=ticket.demo_scenario,
                    is_demo_data=ticket.is_demo_data,
                    created_at=ticket.created_at,
                    updated_at=ticket.updated_at,
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
                ),
                agent_run=_agent_run_response(db, run),
            )
        )
    return items


def _ticket_detail_view(ticket: Ticket) -> TicketDetailResponse:
    """把已加载的 Ticket 关系映射为产品上下文，不补造缺失关联。"""
    customer = ticket.customer
    order = ticket.order
    return TicketDetailResponse(
        business_key=ticket.business_key,
        subject=ticket.subject,
        issue_type=ticket.issue_type,
        description=ticket.description,
        status=ticket.status.value,
        demo_scenario=ticket.demo_scenario,
        is_demo_data=ticket.is_demo_data,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
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


def _agent_run_center_item(
    db: Session, agent_run: AgentRun
) -> AgentRunCenterItemResponse:
    return AgentRunCenterItemResponse(
        agent_run=_agent_run_response(
            db,
            agent_run,
            include_decision_reason=True,
        ),
        ticket=_ticket_detail_view(agent_run.ticket),
    )


@app.get("/agent-runs", response_model=list[AgentRunCenterItemResponse])
async def list_agent_runs(
    db: Session = Depends(get_db),
) -> list[AgentRunCenterItemResponse]:
    """返回全部真实 AgentRun，供 AI Execution Center 使用。"""
    runs = (
        db.query(AgentRun)
        .options(
            joinedload(AgentRun.ticket).joinedload(Ticket.customer),
            joinedload(AgentRun.ticket).joinedload(Ticket.order),
        )
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .all()
    )
    return [_agent_run_center_item(db, run) for run in runs]


@app.get(
    "/agent-runs/{agent_run_key}",
    response_model=AgentRunCenterItemResponse,
)
async def get_agent_run(
    agent_run_key: str, db: Session = Depends(get_db)
) -> AgentRunCenterItemResponse:
    """按稳定业务标识返回一条真实 Run 及其 Ticket 上下文。"""
    run = (
        db.query(AgentRun)
        .options(
            joinedload(AgentRun.ticket).joinedload(Ticket.customer),
            joinedload(AgentRun.ticket).joinedload(Ticket.order),
        )
        .filter(AgentRun.business_key == agent_run_key)
        .one_or_none()
    )
    if run is None:
        raise HTTPException(status_code=404, detail=f"未找到 AgentRun: {agent_run_key}")
    return _agent_run_center_item(db, run)


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


class AuditEventResponse(BaseModel):
    """一次已经真实发生的执行事实的审计事件（T023）。

    每个字段都直接来自数据库中真实存在的 ``AuditEvent`` 行，不含自增主键或任何
    ORM 内部字段。``outcome`` 是事件的具体结果语义（success / unavailable / allow /
    approval_required / created / updated / completed ...），``success`` 是通用的
    正向标记；二者来自真实持久化结果，而非模型文本。
    """

    event_key: str
    event_type: str
    actor_type: str
    occurred_at: datetime
    outcome: str
    success: bool
    action: str
    summary: str
    affected_object_type: str | None
    affected_object_key: str | None
    reference_type: str | None
    reference_key: str | None


@app.get(
    "/agent-runs/{agent_run_key}/audit-events",
    response_model=list[AuditEventResponse],
)
async def list_audit_events(
    agent_run_key: str, db: Session = Depends(get_db)
) -> list[AuditEventResponse]:
    """返回一次 Agent Run 的完整审计时间线。

    事件按 ``occurred_at`` 升序、``id`` 升序排序，顺序确定、可复现；同一条业务
    事实因幂等去重只会出现一次。未知 Agent Run 返回诚实的 404，绝不返回空列表
    冒充"没有事件"。
    """
    agent_run = (
        db.query(AgentRun)
        .filter(AgentRun.business_key == agent_run_key)
        .one_or_none()
    )
    if agent_run is None:
        raise HTTPException(status_code=404, detail=f"未找到 AgentRun: {agent_run_key}")

    events = (
        db.query(AuditEvent)
        .filter(AuditEvent.agent_run_id == agent_run.id)
        .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
        .all()
    )
    return [
        AuditEventResponse(
            event_key=event.business_key,
            event_type=event.event_type.value,
            actor_type=event.actor_type.value,
            occurred_at=event.occurred_at,
            outcome=event.outcome,
            success=event.success,
            action=event.action,
            summary=event.summary,
            affected_object_type=event.affected_object_type,
            affected_object_key=event.affected_object_key,
            reference_type=event.reference_type,
            reference_key=event.reference_key,
        )
        for event in events
    ]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
