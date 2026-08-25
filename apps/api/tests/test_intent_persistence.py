"""T011 intent 持久化到 AgentRun / AgentStep 的集成测试。"""
import json

import pytest

from database import SessionLocal
from intent_service import INTENT_STEP_NAME, extract_and_persist_intent
from intents import IntentExtractionStatus, IntentType
from models import (
    AgentIntent,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    Ticket,
)
from seed_data import seed_demo_data


# 本测试模块创建的 AgentRun 统一使用该前缀，便于精确清理
TEST_RUN_KEY_PREFIX = "agentrun-intent-"


def _valid_raw_output(**overrides) -> str:
    """构造一条合法的 quality issue / replacement 模型原始输出。"""
    payload = {
        "intent_type": "quality_issue_replacement",
        "issue_summary": "右耳耳机无声，疑似质量问题",
        "requested_action": "replacement",
        "confidence": 0.95,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _clear_test_runs() -> None:
    """删除本模块创建的 AgentRun。agent_steps / agent_intents 由数据库级
    ON DELETE CASCADE 一并清除。"""
    db = SessionLocal()
    try:
        db.query(AgentRun).filter(
            AgentRun.business_key.like(f"{TEST_RUN_KEY_PREFIX}%")
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重建确定性状态：重新播种演示数据并清理本模块残留 Run。"""
    _clear_test_runs()
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()

    yield

    _clear_test_runs()


def _create_run(db, key: str) -> AgentRun:
    """基于 seeded demo ticket 创建一个 AgentRun，供 intent 持久化关联。"""
    ticket_id = (
        db.query(Ticket).filter(Ticket.business_key == "ticket-demo-001").one().id
    )
    run = AgentRun(business_key=f"{TEST_RUN_KEY_PREFIX}{key}", ticket_id=ticket_id)
    db.add(run)
    db.flush()
    return run


def test_successful_intent_persists_completed_step_and_queryable_intent():
    """合法 intent 成功时：AgentStep completed，AgentIntent 类型化落库并可查询"""
    db = SessionLocal()
    try:
        run = _create_run(db, "success-001")
        assert run.status is AgentRunStatus.QUEUED

        outcome = extract_and_persist_intent(db, run, _valid_raw_output())

        assert outcome.status is IntentExtractionStatus.SUCCESS

        step = db.query(AgentStep).filter(AgentStep.agent_run_id == run.id).one()
        assert step.name == INTENT_STEP_NAME
        assert step.status is AgentStepStatus.COMPLETED
        assert step.completed_at is not None
        assert step.error_message is None

        intent = db.query(AgentIntent).filter(AgentIntent.agent_run_id == run.id).one()
        assert intent.intent_type == "quality_issue_replacement"
        assert intent.issue_summary == "右耳耳机无声，疑似质量问题"
        assert intent.requested_action == "replacement"
        assert intent.confidence == 0.95

        # Run 从 queued 提升为 running，并记录开始时间
        assert run.status is AgentRunStatus.RUNNING
        assert run.started_at is not None
    finally:
        db.close()

    # 用全新 session 读回，证明 intent 与 step 独立于写入 session 真实落库
    read_db = SessionLocal()
    try:
        persisted_run = (
            read_db.query(AgentRun)
            .filter(AgentRun.business_key == f"{TEST_RUN_KEY_PREFIX}success-001")
            .one()
        )
        assert persisted_run.intent is not None
        assert persisted_run.intent.intent_type == "quality_issue_replacement"
        assert persisted_run.steps[0].status is AgentStepStatus.COMPLETED
    finally:
        read_db.close()


def test_seeded_quality_issue_ticket_yields_supported_intent():
    """使用 seeded demo ticket（quality issue / replacement）验证成功场景"""
    db = SessionLocal()
    try:
        run = _create_run(db, "seeded-001")
        ticket = db.query(Ticket).filter(Ticket.business_key == "ticket-demo-001").one()
        assert ticket.demo_scenario == "low_risk"

        outcome = extract_and_persist_intent(
            db, run, _valid_raw_output(issue_summary="右耳耳机无声，申请换货")
        )

        assert outcome.status is IntentExtractionStatus.SUCCESS
        assert outcome.intent.intent_type is IntentType.QUALITY_ISSUE_REPLACEMENT
        assert outcome.intent.requested_action.value == "replacement"
    finally:
        db.close()


def test_invalid_output_keeps_failed_step_and_no_intent_record():
    """非法模型输出：AgentStep failed，持久化结构化失败原因，且不写 AgentIntent"""
    db = SessionLocal()
    try:
        run = _create_run(db, "fail-001")

        outcome = extract_and_persist_intent(db, run, "这不是 JSON")

        assert outcome.status is IntentExtractionStatus.INVALID_OUTPUT

        step = db.query(AgentStep).filter(AgentStep.agent_run_id == run.id).one()
        assert step.status is AgentStepStatus.FAILED
        assert step.error_message is not None
        assert "invalid_output" in step.error_message

        intent_count = (
            db.query(AgentIntent).filter(AgentIntent.agent_run_id == run.id).count()
        )
        assert intent_count == 0
    finally:
        db.close()


def test_validation_failure_persists_structured_reason():
    """校验失败：AgentStep failed 且失败原因含字段级错误，不写 AgentIntent"""
    db = SessionLocal()
    try:
        run = _create_run(db, "fail-002")
        raw = json.dumps(
            {
                "intent_type": "quality_issue_replacement",
                "requested_action": "replacement",
                "confidence": 0.9,
            }
        )

        outcome = extract_and_persist_intent(db, run, raw)

        assert outcome.status is IntentExtractionStatus.VALIDATION_FAILED

        step = db.query(AgentStep).filter(AgentStep.agent_run_id == run.id).one()
        assert step.status is AgentStepStatus.FAILED
        assert "validation_failed" in step.error_message
        assert "issue_summary" in step.error_message

        assert (
            db.query(AgentIntent).filter(AgentIntent.agent_run_id == run.id).count()
            == 0
        )
    finally:
        db.close()


def test_unsupported_intent_keeps_failed_step_and_no_intent_record():
    """不受支持的意图：AgentStep failed，且绝不伪造成功 intent"""
    db = SessionLocal()
    try:
        run = _create_run(db, "fail-003")

        outcome = extract_and_persist_intent(
            db, run, _valid_raw_output(intent_type="refund_request")
        )

        assert outcome.status is IntentExtractionStatus.UNSUPPORTED_INTENT

        step = db.query(AgentStep).filter(AgentStep.agent_run_id == run.id).one()
        assert step.status is AgentStepStatus.FAILED
        assert "unsupported_intent" in step.error_message

        assert (
            db.query(AgentIntent).filter(AgentIntent.agent_run_id == run.id).count()
            == 0
        )
    finally:
        db.close()
