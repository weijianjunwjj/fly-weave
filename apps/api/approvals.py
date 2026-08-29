"""ApprovalRequest 的显式类型契约（T020）。

T019 的确定性风险门禁只回答"这次执行是否必须先经过人工审批"，它的结论随
调用结束即消失。T020 把这个结论变成一个**独立的持久化业务实体**：一条
``ApprovalRequest`` 精确记录"哪个 Agent Run 的哪个受保护动作，因为什么风险
事实正在等待人工审批"。

三条不可让步的性质：

1. **等待审批不是失败，也不是完成。** ``ApprovalRequestStatus`` 是独立的状态
   枚举，不复用 ``AgentStepStatus`` / ``AgentRunStatus``；pending approval 不会
   被表示成 error、failure 或 completed。审批语义的权威来源是这条记录本身，
   而不是 ``AgentStep.error_message`` 里的一段字符串。
2. **风险原因是历史事实，不是重新计算的结果。** ``RiskSnapshot`` 保存风险规则
   真正触发那一刻的全部结构化依据。售后政策阈值日后被改动，也不会改写一条
   已经存在的 pending approval 说得出的"当时为什么被拦截"。
3. **快照必须自洽。** ``RiskSnapshot`` 只接受 ``requires_approval`` 为真的风险
   判断，因此"用一个未命中风险的判断创建审批请求"在契约层就无法构造。

T020 只建立 pending 审批请求的持久化闭环，不实现 approve / reject 与审批后
Resume —— 那些属于 T021 / T022。本模块因此不定义任何审批**决策**的行为：
``APPROVED`` / ``REJECTED`` 作为 schema 的稳定取值预留，由 T021 的决策服务
经数据库级 CAS 一次性产生；``ApprovalRequestRecord`` 仅如实映射一条已持久化
审批请求（无论 pending 还是已决策）的当前状态与快照。
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from risk import ProtectedAction, RiskAssessment, RiskLevel, RiskRuleCode


class ApprovalRequestStatus(str, Enum):
    """审批请求状态。

    T020 只真实支持 ``PENDING``：审批请求被创建，等待人工处理。

    ``APPROVED`` / ``REJECTED`` 是 T021 的审批结果状态：人工 approve / reject
    通过数据库级 CAS 把 ``PENDING`` 一次性转换为二者之一，此后再也无法回到
    pending，也不能在两者之间翻转。它们早在 T020 就已作为 schema 的稳定取值
    预留，因此 T021 无需迁移枚举类型。
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RiskSnapshot(BaseModel):
    """风险规则触发那一刻的结构化快照。

    字段与 ``RiskAssessment`` 一一对应，因此快照能够独立、完整地解释一次拦截，
    不需要回头再看当前的 Order 或 AfterSalesPolicy。这正是它存在的理由：政策
    阈值日后被修改，历史审批请求的原因也不会随之改变。
    """

    model_config = ConfigDict(extra="forbid")

    action: ProtectedAction
    level: RiskLevel
    rule_code: RiskRuleCode
    requires_approval: bool
    reason: str = Field(min_length=1)

    # 规则求值所依据的结构化业务输入。命中审批的规则在 T019 契约层已保证这四项
    # 非空，快照原样保留它们，使"说不出为什么被拦截"无法构造。
    order_key: str | None = None
    order_amount: Decimal | None = None
    approval_threshold_amount: Decimal | None = None
    policy_key: str | None = None

    @model_validator(mode="after")
    def _reject_snapshot_that_does_not_require_approval(self) -> "RiskSnapshot":
        if not self.requires_approval:
            raise ValueError(
                "审批请求的风险快照必须来自要求人工审批的风险判断"
            )
        return self

    @classmethod
    def from_assessment(cls, risk: RiskAssessment) -> "RiskSnapshot":
        """从 T019 的风险判断构造快照。

        逐字段复制，不做任何重新求值或补齐：快照就是那次判断本身。
        """
        return cls(
            action=risk.action,
            level=risk.level,
            rule_code=risk.rule_code,
            requires_approval=risk.requires_approval,
            reason=risk.reason,
            order_key=risk.order_key,
            order_amount=risk.order_amount,
            approval_threshold_amount=risk.approval_threshold_amount,
            policy_key=risk.policy_key,
        )


class ApprovalRequestRecord(BaseModel):
    """已持久化审批请求的类型化视图。

    每个字段都直接来自数据库中真实存在的 ``ApprovalRequest`` 行，包括它所归属
    的那次 Agent Run 的稳定业务标识。这里不存在任何由调用方或模型填写的字段，
    因此拿到一个 ``ApprovalRequestRecord`` 就等价于"数据库里确实有这条待审批
    请求"。

    ``resolved_at`` 对 pending 必须为空：审批尚未有结果，就不用占位时间伪造它。
    ``agent_run_status`` 携带该审批请求所属 Run 的当前持久化状态，使决策端点在
    返回审批请求时能一并说明"Run 现在停在哪里"（approve 后仍为等待审批，reject
    后转入终止状态）。``decision_reason`` 只有审批已决策时才可能非空。
    """

    approval_key: str
    status: ApprovalRequestStatus
    protected_action: ProtectedAction
    agent_run_key: str
    agent_run_status: str | None = None
    risk: RiskSnapshot
    created_at: datetime
    resolved_at: datetime | None = None
    decision_reason: str | None = None

    @model_validator(mode="after")
    def _pending_must_not_be_resolved(self) -> "ApprovalRequestRecord":
        if (
            self.status is ApprovalRequestStatus.PENDING
            and self.resolved_at is not None
        ):
            raise ValueError("pending 审批请求不得携带审批完成时间")
        return self
