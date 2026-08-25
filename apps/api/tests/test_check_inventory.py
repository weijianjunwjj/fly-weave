"""T014 check_inventory Tool 的确定性测试。

覆盖任务要求的验收点：
- seeded 有货 SKU 可被真实查询到，且可用数量 / 仓库 / 商品信息全部与持久化
  记录一致；
- 持久化但数量为零的 SKU 返回显式 UNAVAILABLE，绝不伪造可用性，也不与失败
  或查无 SKU 混淆；
- 非法输入在进入数据库之前就 schema validation 失败；
- 不存在的 SKU 返回结构化 not-found 失败；
- 重复查询结果稳定；
- 真实 Tool 调用与实际返回结果被记录到 AgentRun / AgentStep /
  AgentInventoryCheck，记录内容只来自真实查询。
"""
import pytest
from pydantic import ValidationError

from database import SessionLocal
from inventory import (
    CheckInventoryRequest,
    InventoryCheckResult,
    InventoryCheckStatus,
)
from inventory_service import (
    INVENTORY_CHECK_STEP_NAME,
    check_and_persist_inventory,
    check_inventory,
)
from models import (
    AgentInventoryCheck,
    AgentRun,
    AgentRunStatus,
    AgentStep,
    AgentStepStatus,
    InventoryItem,
    Ticket,
)
from seed_data import seed_demo_data

# 有货 SKU：低风险换货场景的真实库存来源（T005 播种，数量 12）
AVAILABLE_SKU = "SKU-EARBUD-PRO-01"
# 无货 SKU：拒绝场景的头戴耳机，播种数量为 0，是"无货"的事实来源
UNAVAILABLE_SKU = "SKU-HEADSET-X-02"

# 本测试模块创建的 AgentRun 统一使用该前缀，便于精确清理
TEST_RUN_KEY_PREFIX = "agentrun-inventory-"


def _clear_test_runs() -> None:
    """删除本模块创建的 AgentRun。agent_steps / agent_inventory_checks 由
    数据库级 ON DELETE CASCADE 一并清除。"""
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
    """每个测试前后重新播种 demo 数据，并清理本模块残留 Run，保证查询目标
    始终存在且状态可预测。"""
    _clear_test_runs()
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()

    yield

    _clear_test_runs()
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


def _persisted_inventory(db, sku: str) -> InventoryItem:
    return db.query(InventoryItem).filter(InventoryItem.product_sku == sku).one()


def test_available_sku_returns_persisted_facts():
    """有货 SKU 查询成功，且每个字段都等于数据库中的真实持久化值"""
    db = SessionLocal()
    try:
        result = check_inventory(db, CheckInventoryRequest(product_sku=AVAILABLE_SKU))

        assert result.status is InventoryCheckStatus.SUCCESS
        assert result.failure_reason is None

        item = _persisted_inventory(db, AVAILABLE_SKU)
        assert result.inventory.product_sku == item.product_sku
        assert result.inventory.product_name == item.product_name
        assert result.inventory.available_quantity == item.available_quantity
        assert result.inventory.warehouse == item.warehouse
        assert result.inventory.is_demo_data is item.is_demo_data
        assert result.inventory.available_quantity > 0
    finally:
        db.close()


def test_unavailable_sku_is_explicit_not_fabricated():
    """持久化但数量为零的 SKU 返回显式 UNAVAILABLE，携带真实无货事实"""
    db = SessionLocal()
    try:
        result = check_inventory(db, CheckInventoryRequest(product_sku=UNAVAILABLE_SKU))

        assert result.status is InventoryCheckStatus.UNAVAILABLE
        assert result.failure_reason is None
        assert result.requested_sku == UNAVAILABLE_SKU

        item = _persisted_inventory(db, UNAVAILABLE_SKU)
        assert item.available_quantity == 0
        assert result.inventory.product_sku == UNAVAILABLE_SKU
        assert result.inventory.product_name == item.product_name
        assert result.inventory.available_quantity == 0
        assert result.inventory.warehouse == item.warehouse
        assert result.inventory.is_demo_data is item.is_demo_data
    finally:
        db.close()


def test_missing_sku_returns_structured_not_found():
    """不存在的 SKU 返回结构化 not-found，明确指出被请求的 SKU，且不携带库存事实"""
    db = SessionLocal()
    try:
        result = check_inventory(
            db, CheckInventoryRequest(product_sku="SKU-DOES-NOT-EXIST-99")
        )

        assert result.status is InventoryCheckStatus.SKU_NOT_FOUND
        assert result.inventory is None
        assert result.requested_sku == "SKU-DOES-NOT-EXIST-99"
        assert result.failure_reason == "未找到库存: SKU-DOES-NOT-EXIST-99"
    finally:
        db.close()


def test_repeated_lookup_is_stable():
    """重复查询返回同一份库存事实；跨 session 与重新播种后仍保持一致"""
    request = CheckInventoryRequest(product_sku=AVAILABLE_SKU)

    db = SessionLocal()
    try:
        first = check_inventory(db, request)
        second = check_inventory(db, request)
        assert first == second
    finally:
        db.close()

    db = SessionLocal()
    try:
        third = check_inventory(db, request)
        assert third.status is InventoryCheckStatus.SUCCESS
        assert third.inventory.available_quantity == first.inventory.available_quantity
        assert third.inventory.warehouse == first.inventory.warehouse
        assert third.inventory.product_sku == first.inventory.product_sku
    finally:
        db.close()


def test_repeated_lookup_does_not_mutate_stock():
    """重复查询只读取持久化状态，不改变库存数量（无货的仍然无货）"""
    db = SessionLocal()
    try:
        before = check_inventory(
            db, CheckInventoryRequest(product_sku=UNAVAILABLE_SKU)
        )
        assert before.status is InventoryCheckStatus.UNAVAILABLE

        after = check_inventory(db, CheckInventoryRequest(product_sku=UNAVAILABLE_SKU))
        assert after.status is InventoryCheckStatus.UNAVAILABLE

        persisted = _persisted_inventory(db, UNAVAILABLE_SKU)
        assert persisted.available_quantity == 0
    finally:
        db.close()


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {},                                    # 缺必要字段
        {"product_sku": ""},                   # 空 SKU
        {"product_sku": "   "},                # 纯空白
        {"product_sku": "SKU Earbud 01"},      # 含空格的非法 SKU
        {"product_sku": "SKU-01; DROP TABLE inventory_items"},  # 试图夹带 SQL
        {"product_sku": "%"},                  # 试图使用通配符做模糊匹配
        {"product_sku": "-SKU-EARBUD-PRO-01"}, # 不符合业务标识符格式
        {"product_sku": "S" * 65},             # 超出 product_sku 列宽
        {"product_sku": None},                 # 类型非法
        {"product_sku": 1},                    # 类型非法
    ],
)
def test_invalid_input_fails_schema_validation(invalid_payload):
    """非法输入在 schema 层直接失败，不会进入数据库查询后再模糊失败"""
    with pytest.raises(ValidationError):
        CheckInventoryRequest(**invalid_payload)


def test_request_rejects_arbitrary_extra_filters():
    """请求契约不接受额外字段，调用方无法夹带任意过滤条件"""
    with pytest.raises(ValidationError):
        CheckInventoryRequest(product_sku=AVAILABLE_SKU, warehouse="WH-EAST-01")


def test_model_text_is_rejected_not_treated_as_success():
    """模型原始文本不得成为查询输入，更不得被包装成 success / unavailable"""
    db = SessionLocal()
    try:
        result = check_inventory(db, "库存 SKU-EARBUD-PRO-01 查询成功，有货")

        assert result.status is InventoryCheckStatus.INVALID_REQUEST
        assert result.inventory is None
        assert result.failure_reason is not None
    finally:
        db.close()


def test_success_result_cannot_be_constructed_without_facts():
    """即使在应用内部也无法构造没有库存事实却宣称 success 的伪成功结果"""
    with pytest.raises(ValidationError):
        InventoryCheckResult(status=InventoryCheckStatus.SUCCESS)


def test_unavailable_result_cannot_be_constructed_without_facts():
    """无货结果必须携带真实库存事实，无法凭空构造"""
    with pytest.raises(ValidationError):
        InventoryCheckResult(status=InventoryCheckStatus.UNAVAILABLE)


def test_failure_result_cannot_carry_inventory_facts():
    """失败结果不得携带库存事实，避免失败被伪装成半成功状态"""
    from inventory import InventoryFacts

    facts = InventoryFacts(
        product_sku=AVAILABLE_SKU,
        product_name="Flyweave 无线耳机 Pro",
        available_quantity=12,
        warehouse="WH-EAST-01",
        is_demo_data=True,
    )
    with pytest.raises(ValidationError):
        InventoryCheckResult(
            status=InventoryCheckStatus.SKU_NOT_FOUND,
            inventory=facts,
            requested_sku=AVAILABLE_SKU,
            failure_reason="未找到库存",
        )


def _create_run(db, key: str) -> AgentRun:
    """基于 seeded demo ticket 创建一个 AgentRun，供库存记录关联。"""
    ticket_id = (
        db.query(Ticket).filter(Ticket.business_key == "ticket-demo-001").one().id
    )
    run = AgentRun(business_key=f"{TEST_RUN_KEY_PREFIX}{key}", ticket_id=ticket_id)
    db.add(run)
    db.flush()
    return run


def test_available_check_is_recorded_in_agent_run():
    """有货查询的真实结果被记录到 AgentRun / AgentStep / AgentInventoryCheck"""
    db = SessionLocal()
    try:
        run = _create_run(db, "record-available-001")
        assert run.status is AgentRunStatus.QUEUED

        result = check_and_persist_inventory(
            db, run, CheckInventoryRequest(product_sku=AVAILABLE_SKU)
        )
        assert result.status is InventoryCheckStatus.SUCCESS

        step = db.query(AgentStep).filter(AgentStep.agent_run_id == run.id).one()
        assert step.name == INVENTORY_CHECK_STEP_NAME
        assert step.status is AgentStepStatus.COMPLETED
        assert step.error_message is None

        record = (
            db.query(AgentInventoryCheck)
            .filter(AgentInventoryCheck.agent_run_id == run.id)
            .one()
        )
        assert record.requested_sku == AVAILABLE_SKU
        assert record.status == "success"
        assert record.available_quantity == 12
        assert record.warehouse == "WH-EAST-01"
        assert record.is_demo_data is True
        assert record.failure_reason is None

        # Run 从 queued 提升为 running，并记录开始时间
        assert run.status is AgentRunStatus.RUNNING
        assert run.started_at is not None
    finally:
        db.close()


def test_unavailable_check_is_recorded_in_agent_run():
    """无货查询的真实结果同样被记录：status=unavailable 且数量为 0，而非失败"""
    db = SessionLocal()
    try:
        run = _create_run(db, "record-unavailable-001")

        result = check_and_persist_inventory(
            db, run, CheckInventoryRequest(product_sku=UNAVAILABLE_SKU)
        )
        assert result.status is InventoryCheckStatus.UNAVAILABLE

        step = db.query(AgentStep).filter(AgentStep.agent_run_id == run.id).one()
        assert step.status is AgentStepStatus.COMPLETED
        assert step.error_message is None

        record = (
            db.query(AgentInventoryCheck)
            .filter(AgentInventoryCheck.agent_run_id == run.id)
            .one()
        )
        assert record.requested_sku == UNAVAILABLE_SKU
        assert record.status == "unavailable"
        assert record.available_quantity == 0
        assert record.warehouse == "WH-EAST-01"
        assert record.failure_reason is None
    finally:
        db.close()


def test_not_found_check_records_failed_step_and_reason():
    """查无 SKU 的真实失败结果被记录：AgentStep failed，且不伪造可用性字段"""
    db = SessionLocal()
    try:
        run = _create_run(db, "record-notfound-001")

        result = check_and_persist_inventory(
            db, run, CheckInventoryRequest(product_sku="SKU-DOES-NOT-EXIST-99")
        )
        assert result.status is InventoryCheckStatus.SKU_NOT_FOUND

        step = db.query(AgentStep).filter(AgentStep.agent_run_id == run.id).one()
        assert step.status is AgentStepStatus.FAILED
        assert "sku_not_found" in step.error_message

        record = (
            db.query(AgentInventoryCheck)
            .filter(AgentInventoryCheck.agent_run_id == run.id)
            .one()
        )
        assert record.requested_sku == "SKU-DOES-NOT-EXIST-99"
        assert record.status == "sku_not_found"
        assert record.available_quantity is None
        assert record.warehouse is None
        assert record.is_demo_data is None
        assert record.failure_reason == "未找到库存: SKU-DOES-NOT-EXIST-99"
    finally:
        db.close()
