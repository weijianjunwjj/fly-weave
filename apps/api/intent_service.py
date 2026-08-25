"""把已验证的结构化 intent 持久化到 AgentRun / AgentStep。

成功时：intent extraction 对应的 AgentStep 标记为 completed，并把已验证的
intent 以类型化字段写入 AgentIntent（关联到当前 AgentRun，可事后查询）。
失败时：AgentStep 保持 failed，并持久化足够的结构化失败原因；绝不写入
伪成功的 intent。
"""
from datetime import datetime

from sqlalchemy.orm import Session

from intents import (
    IntentExtractionOutcome,
    IntentExtractionStatus,
    ReplacementIntent,
    extract_intent,
)
from models import (
    AgentIntent,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
)

# intent extraction 是 Golden Path 的第一步，固定使用 step_order=1
INTENT_STEP_NAME = "提取客户意图"
INTENT_STEP_ORDER = 1


def extract_and_persist_intent(
    db: Session, agent_run: AgentRun, raw_model_output: str | None
) -> IntentExtractionOutcome:
    """执行 intent 抽取，并把结果记录到给定的 AgentRun。"""
    outcome = extract_intent(raw_model_output)
    _mark_run_started(agent_run)
    step = _get_or_create_intent_step(db, agent_run)
    now = datetime.utcnow()

    if outcome.status is IntentExtractionStatus.SUCCESS and outcome.intent is not None:
        step.status = AgentStepStatus.COMPLETED
        step.completed_at = now
        step.error_message = None
        _upsert_agent_intent(db, agent_run, outcome.intent)
    else:
        step.status = AgentStepStatus.FAILED
        step.completed_at = now
        step.error_message = _format_failure_message(outcome)

    db.commit()
    return outcome


def _mark_run_started(agent_run: AgentRun) -> None:
    """一次 Run 开始执行时，将 queued 提升为 running 并记录开始时间。

    只负责"已经开始"这一事实；Run 是否 completed / failed 由后续端到端
    编排决定，不在本任务范围内。
    """
    if agent_run.status is AgentRunStatus.QUEUED:
        agent_run.status = AgentRunStatus.RUNNING
        agent_run.started_at = datetime.utcnow()


def _get_or_create_intent_step(db: Session, agent_run: AgentRun) -> AgentStep:
    """取回该 Run 已有的 intent 步骤，或创建一条 pending 起步的步骤记录。"""
    step = (
        db.query(AgentStep)
        .filter(
            AgentStep.agent_run_id == agent_run.id,
            AgentStep.name == INTENT_STEP_NAME,
        )
        .one_or_none()
    )
    if step is None:
        step = AgentStep(
            agent_run_id=agent_run.id,
            step_order=INTENT_STEP_ORDER,
            name=INTENT_STEP_NAME,
            status=AgentStepStatus.RUNNING,
        )
        db.add(step)
        db.flush()
    return step


def _upsert_agent_intent(
    db: Session, agent_run: AgentRun, intent: ReplacementIntent
) -> AgentIntent:
    """把已验证 intent 以类型化字段写入 AgentIntent（每个 Run 至多一条）。"""
    persisted = (
        db.query(AgentIntent)
        .filter(AgentIntent.agent_run_id == agent_run.id)
        .one_or_none()
    )
    if persisted is None:
        persisted = AgentIntent(agent_run_id=agent_run.id)
        db.add(persisted)

    persisted.intent_type = intent.intent_type.value
    persisted.issue_summary = intent.issue_summary
    persisted.requested_action = intent.requested_action.value
    persisted.confidence = intent.confidence
    return persisted


def _format_failure_message(outcome: IntentExtractionOutcome) -> str:
    """把失败结果编码成可检查的结构化失败原因，写入 AgentStep.error_message。"""
    parts = [f"status={outcome.status.value}"]
    if outcome.failure_reason:
        parts.append(f"reason={outcome.failure_reason}")
    if outcome.validation_errors:
        parts.append(f"errors={'; '.join(outcome.validation_errors)}")
    return "; ".join(parts)
