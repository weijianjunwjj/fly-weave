"""ApprovalRequest 的最小内部 service（T020）。

职责只有两件事：

1. 在风险门禁真正命中之后，为"某次 Run 的某个受保护动作"创建或取回**唯一**
   的 pending 审批请求；
2. 按 Agent Run 取回 pending 审批请求，并把它的持久化快照还原成 typed 视图。

刻意不提供的能力：approve、reject、审批状态转换、审批后 Resume、审批列表 /
搜索 / 分页后台。T020 只建立 pending 审批请求的持久化闭环，这些都不在范围内，
因此这里连对应的函数入口都不存在。

**幂等不是靠"先查后插"。** 先查一次只能挡住串行重复；两次并发调用可以同时
查空、同时插入。因此这里的写入被包在 SAVEPOINT 中，真正的裁决者是
``approval_requests`` 上那条 partial unique index：冲突时数据库拒绝写入，本
模块回滚到 SAVEPOINT（不影响调用方在同一事务里已经做出的其它修改），改为
返回数据库中真实存在的那一条。因此并发与重放都无法产生第二条待审批记录。

本模块不 commit：审批请求必须与"Run 进入等待审批"处在同一个事务边界内提交，
提交时机由调用方（``replacement_service`` 的 mutation boundary）掌握。
"""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from approvals import (
    ApprovalRequestRecord,
    ApprovalRequestStatus,
    RiskSnapshot,
)
from models import AgentRun, ApprovalRequest
from risk import ProtectedAction, RiskAssessment

# 审批请求业务标识的确定性前缀。同一次 Run 的同一个受保护动作，标识稳定可预期，
# 与 partial unique index 表达的是同一条业务规则。
APPROVAL_KEY_PREFIX = "approval-"


def create_or_get_pending_approval(
    db: Session, agent_run: AgentRun, risk: RiskAssessment
) -> ApprovalRequest:
    """为一次被风险门禁拦下的受保护动作创建或取回 pending 审批请求。

    只接受 ``requires_approval`` 为真的风险判断：审批请求是"风险规则确实命中"
    的结果，没有命中就没有审批请求。这一点由 ``RiskSnapshot`` 在契约层强制，
    因此用一个低风险判断创建审批请求会在这里直接失败，而不是悄悄写入一条
    语义错误的记录。

    快照在此刻定型：``risk`` 的九项内容被逐字段复制进持久化列，此后无论政策
    阈值如何变化，这条记录都仍然说得出"当时为什么被拦截"。

    重复调用返回同一条记录，绝不产生第二条待审批请求。
    """
    # 契约层校验先行：低风险判断在这里就会被拒绝，不会写入任何东西
    snapshot = RiskSnapshot.from_assessment(risk)

    existing = get_pending_approval(db, agent_run, snapshot.action)
    if existing is not None:
        return existing

    approval = ApprovalRequest(
        business_key=_approval_key(agent_run.business_key, snapshot.action),
        agent_run_id=agent_run.id,
        protected_action=snapshot.action,
        status=ApprovalRequestStatus.PENDING,
        risk_level=snapshot.level,
        risk_rule_code=snapshot.rule_code,
        risk_requires_approval=snapshot.requires_approval,
        reason=snapshot.reason,
        risk_order_key=snapshot.order_key,
        risk_order_amount=snapshot.order_amount,
        risk_approval_threshold_amount=snapshot.approval_threshold_amount,
        risk_policy_key=snapshot.policy_key,
        # pending 尚未有审批结果，因此绝不写入审批完成时间
        resolved_at=None,
    )

    # SAVEPOINT 使这次写入可以被单独回滚：并发冲突时不会连累调用方在同一事务中
    # 已经做出的其它修改（例如刚刚记录的 AgentStep）。
    savepoint = db.begin_nested()
    db.add(approval)
    try:
        db.flush()
    except IntegrityError:
        savepoint.rollback()
        # 冲突已由数据库确认：另一次并发调用抢先写入了同一条待审批请求，
        # 以数据库中真实存在的那一条为准，绝不据此再插一条。
        concurrent = get_pending_approval(db, agent_run, snapshot.action)
        if concurrent is None:
            # 唯一约束拒绝了写入，却查不到对应记录：不猜测原因，也绝不
            # 宣称审批请求已经存在。
            raise
        return concurrent

    savepoint.commit()
    return approval


def get_pending_approval(
    db: Session,
    agent_run: AgentRun,
    protected_action: ProtectedAction = ProtectedAction.CREATE_REPLACEMENT,
) -> ApprovalRequest | None:
    """按 Agent Run 与受保护动作取回 pending 审批请求，没有则返回 ``None``。

    只查 pending：已有审批结果的记录不是"正在等待人工审批"，不应被当作它返回。
    """
    if agent_run is None or agent_run.id is None:
        return None
    return (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.agent_run_id == agent_run.id,
            ApprovalRequest.protected_action == protected_action,
            ApprovalRequest.status == ApprovalRequestStatus.PENDING,
        )
        .one_or_none()
    )


def risk_snapshot_of(approval: ApprovalRequest) -> RiskSnapshot:
    """把持久化的风险快照还原成 typed 视图。

    纯粹的字段读取：不查询当前 Order / AfterSalesPolicy，也不调用任何风险规则
    求值。这正是快照的意义 —— 展示的是触发时刻的历史事实，而不是此刻重算的
    结论。
    """
    return RiskSnapshot(
        action=approval.protected_action,
        level=approval.risk_level,
        rule_code=approval.risk_rule_code,
        requires_approval=approval.risk_requires_approval,
        reason=approval.reason,
        order_key=approval.risk_order_key,
        order_amount=approval.risk_order_amount,
        approval_threshold_amount=approval.risk_approval_threshold_amount,
        policy_key=approval.risk_policy_key,
    )


def approval_record(approval: ApprovalRequest) -> ApprovalRequestRecord:
    """把已持久化的审批请求映射为类型化视图。

    对外只暴露稳定的业务标识与快照内容，不暴露自增主键或任何 ORM 内部字段。
    """
    return ApprovalRequestRecord(
        approval_key=approval.business_key,
        status=approval.status,
        protected_action=approval.protected_action,
        agent_run_key=approval.agent_run.business_key,
        risk=risk_snapshot_of(approval),
        created_at=approval.created_at,
        resolved_at=approval.resolved_at,
    )


def _approval_key(run_key: str, protected_action: ProtectedAction) -> str:
    """确定性派生审批请求标识。

    同一次 Run 的同一个受保护动作只会得到同一个标识，与 partial unique index
    表达同一条业务规则；标识本身就说明了它属于谁、为哪个动作而等。
    """
    return f"{APPROVAL_KEY_PREFIX}{run_key}-{protected_action.value}"
