"""AuditEvent 的最小写入 service（T023）。

本模块只做一件事：把"一次已经真实发生的领域事实"写成一条 ``AuditEvent``。它不
建立事件总线、不做审计平台、不做任何业务判断，也不凭空制造事件——调用方只在
真实领域事实（判定已产出、Tool 已真实返回、换货单已落库、工单已回写、审批已
完成状态迁移）之后才调用它。

幂等去重依赖 ``business_key`` 的确定性派生与 ``audit_events.business_key`` 上的
数据库唯一约束，与 ``approval_service`` 处理 pending approval 用的是同一套
SAVEPOINT 思路：先查一次挡住串行重复，真正的裁决者是数据库唯一约束；冲突时
回滚到 SAVEPOINT 并返回数据库中真实存在的那一条。因此 API retry、Resume retry、
process restart、并发 resume 或同一审批重试，都不会产生第二条语义相同的事件，
且绝不依赖进程内内存状态。

本模块不 commit：审计事件必须与"产生它的业务事实"处在同一个事务边界内提交，
提交时机由调用方（各 service 的 mutation boundary）掌握，这样既不会出现"业务
已提交但审计缺失"的可观察窗口，也不会出现"审计声称成功但业务其实没落库"。
"""
import json
from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models import ActorType, AuditEvent, AuditEventType

# 审计事件业务标识的确定性前缀。同一次 Run 的同一事件类型，标识稳定可预期，
# 与 business_key 唯一约束表达的是同一条业务规则。
AUDIT_KEY_PREFIX = "audit-"

# metadata_json 里允许存的最大 JSON 文本长度，防止超长人工输入撑爆审计列。
_MAX_METADATA_LENGTH = 2000


def record_audit_event(
    db: Session,
    *,
    agent_run,
    event_type: AuditEventType,
    actor_type: ActorType,
    outcome: str,
    success: bool,
    action: str,
    summary: str,
    occurred_at: datetime | None = None,
    affected_object_type: str | None = None,
    affected_object_key: str | None = None,
    reference_type: str | None = None,
    reference_key: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    """幂等记录一条审计事件，返回已持久化（或已存在）的那一条。

    所有字段都来自调用方已经拿到的真实结果；本函数不做任何推断、不读取任何
    未被传入的持久化状态，也不自行把某个动作标为成功。是否成功由调用方依据
    真实业务 / 持久化结果决定后作为 ``success`` 传入。

    ``business_key`` 由 ``event_type`` 与 ``agent_run.business_key`` 确定性派生，
    因此对同一次 Run 的同一事件类型，重复调用只会返回同一条记录。
    """
    business_key = _audit_key(event_type.value, agent_run.business_key)

    existing = (
        db.query(AuditEvent).filter(AuditEvent.business_key == business_key).one_or_none()
    )
    if existing is not None:
        return existing

    event = AuditEvent(
        business_key=business_key,
        agent_run_id=agent_run.id,
        event_type=event_type,
        actor_type=actor_type,
        outcome=outcome,
        success=success,
        occurred_at=occurred_at if occurred_at is not None else datetime.utcnow(),
        affected_object_type=affected_object_type,
        affected_object_key=affected_object_key,
        action=action,
        summary=summary,
        reference_type=reference_type,
        reference_key=reference_key,
        metadata_json=_dump_metadata(metadata),
    )

    # SAVEPOINT 使这次写入可以被单独回滚：并发冲突时不会连累调用方在同一事务中
    # 已经做出的其它修改。真正的裁决者是 business_key 唯一约束。
    savepoint = db.begin_nested()
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        savepoint.rollback()
        # 冲突已由数据库确认：另一次并发调用抢先写入了同一条事件，以数据库中真实
        # 存在的那一条为准，绝不据此再插一条。
        concurrent = (
            db.query(AuditEvent)
            .filter(AuditEvent.business_key == business_key)
            .one_or_none()
        )
        if concurrent is None:
            raise
        return concurrent

    savepoint.commit()
    return event


def _audit_key(event_type: str, run_key: str) -> str:
    """确定性派生审计事件标识：同一次 Run 的同一事件类型只会得到同一个标识。"""
    return f"{AUDIT_KEY_PREFIX}{event_type}-{run_key}"


def _dump_metadata(metadata: dict | None) -> str | None:
    """把安全的结构化 metadata 序列化为 JSON 文本，空则返回 None。"""
    if not metadata:
        return None
    serialized = json.dumps(metadata, ensure_ascii=False, sort_keys=True)
    return serialized[:_MAX_METADATA_LENGTH]
