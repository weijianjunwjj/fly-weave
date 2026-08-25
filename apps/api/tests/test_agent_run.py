from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from database import SessionLocal
from models import (
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    Ticket,
)
from seed_data import clear_demo_data, seed_demo_data


# 本测试模块创建的 AgentRun 统一使用该前缀，便于精确清理，
# 不影响其它测试或既有演示数据
TEST_RUN_KEY_PREFIX = "agentrun-test-"


def _clear_test_agent_runs() -> None:
    """删除本模块创建的 AgentRun。agent_steps 由数据库级 ON DELETE CASCADE 一并清除"""
    db = SessionLocal()
    try:
        db.query(AgentRun).filter(
            AgentRun.business_key.like(f"{TEST_RUN_KEY_PREFIX}%")
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def deterministic_agent_run_state():
    """
    每个测试前后都重建确定性状态：

    - 重新播种演示数据，使 AgentRun 始终有一个真实持久化的工单可以关联；
    - 清除本模块残留的 AgentRun，使测试可重复运行而不会因 business_key
      唯一约束失败。
    """
    _clear_test_agent_runs()
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()

    yield

    _clear_test_agent_runs()


def _demo_ticket_id(db) -> int:
    """返回演示工单的主键，供 AgentRun 关联真实持久化记录"""
    return db.query(Ticket).filter(Ticket.business_key == "ticket-demo-001").one().id


def test_agent_run_persists_and_reads_back_key_fields():
    """验证 AgentRun 能写入真实数据库，并在新 session 中读回全部关键字段"""
    started_at = datetime(2026, 8, 24, 10, 0, 0)
    completed_at = datetime(2026, 8, 24, 10, 5, 0)

    write_db = SessionLocal()
    try:
        run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}persist-001",
            ticket_id=_demo_ticket_id(write_db),
            status=AgentRunStatus.COMPLETED,
            started_at=started_at,
            completed_at=completed_at,
        )
        write_db.add(run)
        write_db.commit()
        ticket_id = run.ticket_id
    finally:
        write_db.close()

    # 用一个全新的 session 读回，证明状态独立于写入时的 session /
    # HTTP 请求生命周期真实落库，而不是留在内存标识映射中
    read_db = SessionLocal()
    try:
        persisted = read_db.query(AgentRun).filter(
            AgentRun.business_key == f"{TEST_RUN_KEY_PREFIX}persist-001"
        ).one()

        assert persisted.id is not None
        assert persisted.ticket_id == ticket_id
        assert persisted.status is AgentRunStatus.COMPLETED
        assert persisted.started_at == started_at
        assert persisted.completed_at == completed_at
        assert persisted.created_at is not None
        assert persisted.ticket.business_key == "ticket-demo-001"
    finally:
        read_db.close()


def test_agent_run_defaults_to_queued_without_execution_timestamps():
    """验证仅提供必填字段时的默认值：状态为 queued，且不伪造开始 / 结束时间"""
    db = SessionLocal()
    try:
        run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}defaults-001",
            ticket_id=_demo_ticket_id(db),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        assert run.status is AgentRunStatus.QUEUED
        assert run.created_at is not None
        assert run.started_at is None
        assert run.completed_at is None
        assert run.error_message is None
    finally:
        db.close()


def test_agent_run_supports_all_required_run_states():
    """验证六种必需 Run 状态都能持久化并原样读回"""
    required_states = [
        AgentRunStatus.QUEUED,
        AgentRunStatus.RUNNING,
        AgentRunStatus.WAITING_FOR_APPROVAL,
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    ]

    db = SessionLocal()
    try:
        ticket_id = _demo_ticket_id(db)
        for index, status in enumerate(required_states):
            db.add(
                AgentRun(
                    business_key=f"{TEST_RUN_KEY_PREFIX}state-{index:03d}",
                    ticket_id=ticket_id,
                    status=status,
                )
            )
        db.commit()
    finally:
        db.close()

    read_db = SessionLocal()
    try:
        persisted = {
            run.business_key: run.status
            for run in read_db.query(AgentRun).filter(
                AgentRun.business_key.like(f"{TEST_RUN_KEY_PREFIX}state-%")
            )
        }
        assert len(persisted) == len(required_states)
        for index, status in enumerate(required_states):
            assert persisted[f"{TEST_RUN_KEY_PREFIX}state-{index:03d}"] is status
    finally:
        read_db.close()


def test_failed_agent_run_remains_visibly_failed():
    """验证失败的 Run 及其失败原因持久保留，不会被静默改写或丢失"""
    db = SessionLocal()
    try:
        db.add(
            AgentRun(
                business_key=f"{TEST_RUN_KEY_PREFIX}failed-001",
                ticket_id=_demo_ticket_id(db),
                status=AgentRunStatus.FAILED,
                error_message="工具调用超时，执行终止",
            )
        )
        db.commit()
    finally:
        db.close()

    read_db = SessionLocal()
    try:
        failed_run = read_db.query(AgentRun).filter(
            AgentRun.business_key == f"{TEST_RUN_KEY_PREFIX}failed-001"
        ).one()
        assert failed_run.status is AgentRunStatus.FAILED
        assert failed_run.error_message == "工具调用超时，执行终止"
    finally:
        read_db.close()


def test_agent_run_business_key_must_be_unique():
    """验证 business_key 唯一约束在数据库层生效"""
    db = SessionLocal()
    try:
        ticket_id = _demo_ticket_id(db)
        db.add(
            AgentRun(
                business_key=f"{TEST_RUN_KEY_PREFIX}unique-001",
                ticket_id=ticket_id,
            )
        )
        db.commit()

        db.add(
            AgentRun(
                business_key=f"{TEST_RUN_KEY_PREFIX}unique-001",
                ticket_id=ticket_id,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_agent_run_requires_ticket_reference():
    """验证 Run 必须关联工单，ticket_id 不允许为空"""
    db = SessionLocal()
    try:
        db.add(
            AgentRun(
                business_key=f"{TEST_RUN_KEY_PREFIX}no-ticket-001",
                ticket_id=None,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_agent_steps_are_recorded_and_queried_in_deterministic_order():
    """验证步骤可被记录，并按 step_order 确定性读回，与插入顺序无关"""
    db = SessionLocal()
    try:
        run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}steps-001",
            ticket_id=_demo_ticket_id(db),
            status=AgentRunStatus.RUNNING,
        )
        db.add(run)
        db.flush()

        # 故意乱序插入，验证顺序来自 step_order 而不是插入顺序
        db.add_all(
            [
                AgentStep(
                    agent_run_id=run.id,
                    step_order=3,
                    name="查询订单信息",
                    status=AgentStepStatus.PENDING,
                ),
                AgentStep(
                    agent_run_id=run.id,
                    step_order=1,
                    name="理解客户请求",
                    status=AgentStepStatus.COMPLETED,
                ),
                AgentStep(
                    agent_run_id=run.id,
                    step_order=2,
                    name="检索售后政策",
                    status=AgentStepStatus.RUNNING,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    read_db = SessionLocal()
    try:
        persisted = read_db.query(AgentRun).filter(
            AgentRun.business_key == f"{TEST_RUN_KEY_PREFIX}steps-001"
        ).one()

        assert [step.name for step in persisted.steps] == [
            "理解客户请求",
            "检索售后政策",
            "查询订单信息",
        ]
        assert [step.status for step in persisted.steps] == [
            AgentStepStatus.COMPLETED,
            AgentStepStatus.RUNNING,
            AgentStepStatus.PENDING,
        ]
        assert all(step.agent_run_id == persisted.id for step in persisted.steps)
    finally:
        read_db.close()


def test_agent_step_defaults_to_pending_without_execution_timestamps():
    """验证步骤默认状态为 pending，且不伪造开始 / 结束时间"""
    db = SessionLocal()
    try:
        run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}step-defaults-001",
            ticket_id=_demo_ticket_id(db),
        )
        db.add(run)
        db.flush()

        step = AgentStep(agent_run_id=run.id, step_order=1, name="理解客户请求")
        db.add(step)
        db.commit()
        db.refresh(step)

        assert step.status is AgentStepStatus.PENDING
        assert step.created_at is not None
        assert step.started_at is None
        assert step.completed_at is None
        assert step.error_message is None
    finally:
        db.close()


def test_agent_step_supports_all_required_step_states():
    """验证五种必需步骤状态都能持久化并原样读回"""
    required_states = [
        AgentStepStatus.PENDING,
        AgentStepStatus.RUNNING,
        AgentStepStatus.COMPLETED,
        AgentStepStatus.FAILED,
        AgentStepStatus.SKIPPED,
    ]

    db = SessionLocal()
    try:
        run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}step-states-001",
            ticket_id=_demo_ticket_id(db),
        )
        db.add(run)
        db.flush()
        db.add_all(
            [
                AgentStep(
                    agent_run_id=run.id,
                    step_order=index + 1,
                    name=f"步骤 {index + 1}",
                    status=status,
                )
                for index, status in enumerate(required_states)
            ]
        )
        db.commit()
    finally:
        db.close()

    read_db = SessionLocal()
    try:
        persisted = read_db.query(AgentRun).filter(
            AgentRun.business_key == f"{TEST_RUN_KEY_PREFIX}step-states-001"
        ).one()
        assert [step.status for step in persisted.steps] == required_states
    finally:
        read_db.close()


def test_agent_step_order_must_be_unique_within_run():
    """验证同一次 Run 内的 step_order 唯一，保证时间线顺序无歧义"""
    db = SessionLocal()
    try:
        run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}step-unique-001",
            ticket_id=_demo_ticket_id(db),
        )
        db.add(run)
        db.flush()

        db.add(AgentStep(agent_run_id=run.id, step_order=1, name="理解客户请求"))
        db.commit()

        db.add(AgentStep(agent_run_id=run.id, step_order=1, name="检索售后政策"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()


def test_same_step_order_is_allowed_across_different_runs():
    """验证 step_order 的唯一性作用域是单次 Run，而不是全局"""
    db = SessionLocal()
    try:
        ticket_id = _demo_ticket_id(db)
        first_run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}step-scope-001", ticket_id=ticket_id
        )
        second_run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}step-scope-002", ticket_id=ticket_id
        )
        db.add_all([first_run, second_run])
        db.flush()

        db.add_all(
            [
                AgentStep(agent_run_id=first_run.id, step_order=1, name="理解客户请求"),
                AgentStep(agent_run_id=second_run.id, step_order=1, name="理解客户请求"),
            ]
        )
        db.commit()

        assert db.query(AgentStep).filter(
            AgentStep.agent_run_id.in_([first_run.id, second_run.id])
        ).count() == 2
    finally:
        db.close()


def test_clear_demo_data_remains_repeatable_with_persisted_agent_runs():
    """
    验证已持久化的 AgentRun 不会破坏演示数据重置的可重复性。

    clear_demo_data 直接批量删除 tickets，若 agent_runs 残留引用会触发外键
    冲突。数据库级 ON DELETE CASCADE 保证 Run 及其步骤随工单一并清除。
    """
    db = SessionLocal()
    try:
        run = AgentRun(
            business_key=f"{TEST_RUN_KEY_PREFIX}cascade-001",
            ticket_id=_demo_ticket_id(db),
            status=AgentRunStatus.RUNNING,
        )
        db.add(run)
        db.flush()
        db.add(AgentStep(agent_run_id=run.id, step_order=1, name="理解客户请求"))
        db.commit()
        run_id = run.id

        clear_demo_data(db)

        assert db.query(AgentRun).filter(AgentRun.id == run_id).one_or_none() is None
        assert db.query(AgentStep).filter(AgentStep.agent_run_id == run_id).count() == 0
    finally:
        db.close()
