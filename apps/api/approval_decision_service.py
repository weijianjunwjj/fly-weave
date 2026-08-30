"""人工审批决策的一次性状态转换（T021）。

T020 把"受保护动作正在等待人工审批"变成了一条持久化的 ``ApprovalRequest``；
T021 只做一件事：让一个人把这条 pending 审批请求**一次性**地决定为
``APPROVED`` 或 ``REJECTED``。

三条不可让步的性质：

1. **转换只允许从 PENDING 出发。** ``PENDING -> APPROVED`` 与
   ``PENDING -> REJECTED`` 是仅有的两条合法边；已经决定过的请求无法再翻转，
   也不存在回到 pending 的路径。
2. **只有 PENDING 能完成转换，且由数据库裁决。** 状态转换是一条带
   ``status = 'pending'`` 条件的原子 UPDATE（compare-and-set）：``rowcount`` 为
   1 表示本次完成了转换，为 0 表示已经有人抢先决定过。approve 与 reject 并发
   时，数据库行锁会让后到者等到先到者提交，再重新按"当前状态"判定为幂等或冲突。
   这绝不依赖进程内锁，也不依赖"先查后写"。
3. **决策只记录事实，不推进业务。** approve 只把审批请求标为 APPROVED，不执行
   create_replacement、不 update_ticket、不 Resume AgentRun、不把 Run 标
   COMPLETED —— 恢复执行是 T022 的事。reject 把审批请求标为 REJECTED，并把还在
   ``WAITING_FOR_APPROVAL`` 的 Run 转入既有的 ``CANCELLED`` 终止态；它不产生
   换货单、不更新工单，被拒绝的请求日后也不再可能作为执行受保护动作的有效批准。

幂等与冲突的判定都发生在 CAS **未命中**之后，以数据库里真实存在的当前状态为准：

- 不存在 -> ``ApprovalRequestNotFoundError``（404）；
- 当前状态就是目标状态 -> 直接返回当前持久化结果，不刷新 ``resolved_at``、
  ``decision_reason`` 或 risk snapshot（幂等）；
- 当前状态与目标相反 -> ``ApprovalConflictError``（409）。

本模块不 commit 之外不做任何审批 UI、通知、用户/角色系统，也不重算历史 risk。
"""
from datetime import datetime

from sqlalchemy.orm import Session

from approvals import ApprovalRequestStatus
from audit_service import record_audit_event
from models import ActorType, AgentRun, AgentRunStatus, ApprovalRequest, AuditEventType


class ApprovalRequestNotFoundError(Exception):
    """审批请求不存在（key 对不上任何已持久化记录）。"""

    def __init__(self, approval_key: str) -> None:
        super().__init__(f"未找到审批请求: {approval_key}")
        self.approval_key = approval_key


class ApprovalConflictError(Exception):
    """审批请求已进入与本次决策相反的终态，无法再翻转。"""

    def __init__(
        self, approval_key: str, current_status: ApprovalRequestStatus
    ) -> None:
        super().__init__(
            f"审批请求 {approval_key} 已处于 {current_status.value}，"
            "无法执行相反的决策"
        )
        self.approval_key = approval_key
        self.current_status = current_status


def approve(
    db: Session, approval_key: str, decision_reason: str | None = None
) -> ApprovalRequest:
    """批准一条 pending 审批请求。

    只把审批请求记录为 APPROVED，不执行任何受保护动作、不更新工单、不恢复
    Run、不把 Run 标 COMPLETED。
    """
    return _decide(
        db, approval_key, ApprovalRequestStatus.APPROVED, decision_reason
    )


def reject(
    db: Session, approval_key: str, decision_reason: str | None = None
) -> ApprovalRequest:
    """拒绝一条 pending 审批请求。

    把审批请求记录为 REJECTED，并让仍停在 ``WAITING_FOR_APPROVAL`` 的 Run 转入
    既有的 ``CANCELLED`` 终止态。不产生换货单、不更新工单。
    """
    return _decide(
        db, approval_key, ApprovalRequestStatus.REJECTED, decision_reason
    )


def _decide(
    db: Session,
    approval_key: str,
    target: ApprovalRequestStatus,
    decision_reason: str | None,
) -> ApprovalRequest:
    """执行一次一次性的审批决策，返回决策后的持久化审批请求。

    决策结果是数据库级 compare-and-set 的产物，而不是进程内"先读后写"的产物；
    幂等与冲突也只在 CAS 未命中后、以数据库真实状态为准进行判定。
    """
    now = datetime.utcnow()

    # --- 1. 数据库级 CAS：只有 PENDING 能完成一次转换 ---
    # WHERE 里的 status = 'pending' 就是原子条件。并发下后到者的 UPDATE 会阻塞在
    # 先到者持有的行锁上，等先到者提交后再按最新状态重新匹配，因此 approve 与
    # reject 并发时最多只有一次能得到 rowcount == 1。
    matched = _cas_transition(db, approval_key, target, decision_reason, now)

    if matched == 1:
        # 本次真正完成了转换。reject 需要把等待审批的 Run 转入终止态，且必须与
        # 审批请求的状态转换处在同一个事务里一起提交，绝不出现"已拒绝却仍停在
        # 等待审批"的中间态。
        if target is ApprovalRequestStatus.REJECTED:
            _cancel_waiting_run(db, approval_key, now)
        # T023：只有真正赢得 CAS 状态迁移的一方产生 HUMAN 审批审计事件；幂等重试
        # 与并发冲突走下面的 CAS 未命中分支，绝不产生第二条事件。事件随本次决策
        # 事务一起提交。
        _record_decision_audit(db, approval_key, target, decision_reason, now)
        db.commit()
        db.expire_all()
        return _load_approval(db, approval_key)

    # --- 2. CAS 未命中：已经有人抢先决定，或这条请求根本不存在 ---
    # 回滚本次空转换（不产生任何写），再以数据库里真实存在的当前状态作答。
    db.rollback()
    db.expire_all()

    approval = _load_approval(db, approval_key)
    if approval is None:
        raise ApprovalRequestNotFoundError(approval_key)
    if approval.status is target:
        # 同一决策重试：直接返回当前持久化结果，不刷新 resolved_at / decision_reason
        # 也不改动 risk snapshot。
        return approval
    # 相反决策：APPROVED 后 reject 或 REJECTED 后 approve，一律 409。
    raise ApprovalConflictError(approval_key, approval.status)


def _cas_transition(
    db: Session,
    approval_key: str,
    target: ApprovalRequestStatus,
    decision_reason: str | None,
    now: datetime,
) -> int:
    """以 status = 'pending' 为原子条件执行状态转换，返回实际匹配的行数。

    只有 pending 行才会被这条 UPDATE 命中；approved / rejected 行天然不满足
    条件，因此 CAS 的语义被数据库唯一确定，与调用方如何"读"无关。
    """
    return (
        db.query(ApprovalRequest)
        .filter(
            ApprovalRequest.business_key == approval_key,
            ApprovalRequest.status == ApprovalRequestStatus.PENDING,
        )
        .update(
            {
                ApprovalRequest.status: target,
                ApprovalRequest.resolved_at: now,
                ApprovalRequest.decision_reason: decision_reason,
            },
            synchronize_session=False,
        )
    )


def _cancel_waiting_run(
    db: Session, approval_key: str, now: datetime
) -> None:
    """把仍停在等待审批的 Run 转入既有的 CANCELLED 终止态。

    只在真正完成了 REJECTED 转换之后调用：Run 此刻仍应是
    ``WAITING_FOR_APPROVAL``，这里仍做状态守卫，避免在异常数据下误改一个已经
    结束的 Run。CANCELLED 是现有 ``AgentRunStatus`` 里语义最合适的终止态——拒绝
    不是执行失败，而是人为终止这次等待。
    """
    approval = (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.business_key == approval_key)
        .one_or_none()
    )
    if approval is None:
        return

    run = (
        db.query(AgentRun)
        .filter(AgentRun.id == approval.agent_run_id)
        .one_or_none()
    )
    if run is None or run.status is not AgentRunStatus.WAITING_FOR_APPROVAL:
        return
    run.status = AgentRunStatus.CANCELLED
    # 终止不是成功完成，但 Run 确实已经结束，与 FAILED 终态保持同一口径记录时间
    run.completed_at = now
    # 拒绝不是执行失败，不伪造 error_message
    run.error_message = None


def _load_approval(
    db: Session, approval_key: str
) -> ApprovalRequest | None:
    """按业务标识取回审批请求，不存在则返回 None。"""
    return (
        db.query(ApprovalRequest)
        .filter(ApprovalRequest.business_key == approval_key)
        .one_or_none()
    )


def _record_decision_audit(
    db: Session,
    approval_key: str,
    target: ApprovalRequestStatus,
    decision_reason: str | None,
    now: datetime,
) -> None:
    """把一次真正赢得状态迁移的人工审批决策记为 HUMAN 审计事件（T023）。

    只记录审批请求 key、受保护动作、决策时刻与可选理由；当前系统没有真实用户
    体系，因此绝不虚构 actor 的 username / role / employee id。actor_type 只表达
    "这是人工做的决定"这一事实。
    """
    approval = _load_approval(db, approval_key)
    if approval is None or approval.agent_run is None:
        return

    approved = target is ApprovalRequestStatus.APPROVED
    record_audit_event(
        db,
        agent_run=approval.agent_run,
        event_type=(
            AuditEventType.APPROVAL_APPROVED
            if approved
            else AuditEventType.APPROVAL_REJECTED
        ),
        actor_type=ActorType.HUMAN,
        outcome=target.value,
        success=approved,
        action="approve" if approved else "reject",
        summary=(
            f"人工{'批准' if approved else '拒绝'}审批请求: approval={approval_key} "
            f"action={approval.protected_action.value}"
        ),
        occurred_at=now,
        affected_object_type="approval_request",
        affected_object_key=approval_key,
        reference_type="approval",
        reference_key=approval_key,
        metadata={
            "protected_action": approval.protected_action.value,
            "decision_reason": decision_reason,
        },
    )
