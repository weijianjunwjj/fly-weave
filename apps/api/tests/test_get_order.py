"""T013 get_order Tool 的确定性测试。

覆盖任务要求的验收点：
- seeded 订单可被真实查询到，且 order id / purchase date / product-SKU /
  order status / customer reference 全部与持久化记录一致；
- 不存在的订单返回结构化 not-found 失败；
- 非法输入在进入数据库之前就 schema validation 失败；
- 重复查询结果稳定；
- 不存在任何 fabricated / model-driven 的成功路径，成功只能由数据库结果决定。
"""
from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from database import SessionLocal
from models import Order, OrderStatus
from order_service import get_order
from orders import (
    GetOrderRequest,
    OrderFacts,
    OrderLookupResult,
    OrderLookupStatus,
)
from seed_data import seed_demo_data

# Golden Path 的低风险场景订单，由 T005 播种，是本任务的成功路径真实来源
LOW_RISK_ORDER_KEY = "order-demo-001"


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据，保证查询目标始终存在且状态可预测。

    测试内删除订单行以验证失败路径，随后由 fixture 在下一次播种时重建，
    不会污染其它测试。
    """
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()

    yield

    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


def _persisted_order(db, order_key: str) -> Order:
    return db.query(Order).filter(Order.business_key == order_key).one()


def test_seeded_order_lookup_returns_persisted_facts():
    """seeded 订单查询成功，且每个字段都等于数据库中的真实持久化值"""
    db = SessionLocal()
    try:
        result = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))

        assert result.status is OrderLookupStatus.SUCCESS
        assert result.failure_reason is None

        order = _persisted_order(db, LOW_RISK_ORDER_KEY)
        assert result.order.order_key == order.business_key
        assert result.order.purchased_at == order.purchased_at
        assert result.order.product_sku == order.product_sku
        assert result.order.product_name == order.product_name
        assert result.order.status is order.status
        assert result.order.customer_key == order.customer.business_key
        assert result.order.amount == order.amount
        assert result.order.is_demo_data is order.is_demo_data
    finally:
        db.close()


def test_seeded_order_facts_match_expected_demo_values():
    """成功结果的具体取值与 T005 播种的演示订单一致，未被任何一层改写"""
    db = SessionLocal()
    try:
        result = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))

        assert result.status is OrderLookupStatus.SUCCESS
        assert result.requested_order_key == LOW_RISK_ORDER_KEY

        facts = result.order
        assert facts.order_key == LOW_RISK_ORDER_KEY
        assert facts.customer_key == "customer-demo-001"
        assert facts.product_sku == "SKU-EARBUD-PRO-01"
        assert facts.product_name == "Flyweave 无线耳机 Pro"
        assert facts.status is OrderStatus.DELIVERED
        assert facts.amount == Decimal("299.00")
        # 演示数据必须显式标记，避免与生产订单混淆
        assert facts.is_demo_data is True

        # purchase date 是真实的持久化时间：低风险订单播种于 5 天前，
        # 因此必然落在 30 天换货窗口内
        age_days = (datetime.utcnow() - facts.purchased_at).days
        assert age_days == 5
    finally:
        db.close()


@pytest.mark.parametrize(
    "order_key, expected_sku, expected_customer_key, expected_amount",
    [
        ("order-demo-001", "SKU-EARBUD-PRO-01", "customer-demo-001", Decimal("299.00")),
        ("order-demo-002", "SKU-EARBUD-PRO-01", "customer-demo-002", Decimal("1299.00")),
        ("order-demo-003", "SKU-HEADSET-X-02", "customer-demo-003", Decimal("399.00")),
    ],
)
def test_each_seeded_order_is_queryable(
    order_key, expected_sku, expected_customer_key, expected_amount
):
    """三个 seeded 场景订单都能通过同一 Tool 边界查到各自真实的订单事实"""
    db = SessionLocal()
    try:
        result = get_order(db, GetOrderRequest(order_key=order_key))

        assert result.status is OrderLookupStatus.SUCCESS
        assert result.order.order_key == order_key
        assert result.order.product_sku == expected_sku
        assert result.order.customer_key == expected_customer_key
        assert result.order.amount == expected_amount
    finally:
        db.close()


def test_repeated_lookup_is_stable():
    """重复查询返回同一份订单事实；跨 session 与重新播种后仍保持一致"""
    db = SessionLocal()
    try:
        first = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))
        second = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))
        assert first == second
    finally:
        db.close()

    # 换一个 session 再查，结果同样稳定
    db = SessionLocal()
    try:
        third = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))
        assert third.status is OrderLookupStatus.SUCCESS
        assert third.order.order_key == first.order.order_key
        assert third.order.product_sku == first.order.product_sku
        assert third.order.customer_key == first.order.customer_key
        assert third.order.status is first.order.status
        assert third.order.amount == first.order.amount
    finally:
        db.close()


def test_missing_order_returns_structured_not_found():
    """不存在的订单返回结构化 not-found，明确指出被请求的标识，且不携带订单事实"""
    db = SessionLocal()
    try:
        result = get_order(db, GetOrderRequest(order_key="order-demo-does-not-exist"))

        assert result.status is OrderLookupStatus.ORDER_NOT_FOUND
        assert result.order is None
        assert result.requested_order_key == "order-demo-does-not-exist"
        assert result.failure_reason == "未找到订单: order-demo-does-not-exist"
    finally:
        db.close()


def test_failure_reason_does_not_leak_internal_details():
    """失败原因只描述业务事实，不泄漏 SQL、内部异常或实现细节"""
    db = SessionLocal()
    try:
        result = get_order(db, GetOrderRequest(order_key="order-demo-999"))

        assert result.status is OrderLookupStatus.ORDER_NOT_FOUND
        leaked = ("SELECT", "sqlalchemy", "Traceback", "psycopg", "orders.business_key")
        for fragment in leaked:
            assert fragment.lower() not in result.failure_reason.lower()
    finally:
        db.close()


def test_deleted_order_stops_succeeding():
    """成功只能由数据库结果决定：订单被删除后，同一次查询立刻转为 not-found"""
    db = SessionLocal()
    try:
        before = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))
        assert before.status is OrderLookupStatus.SUCCESS

        db.delete(_persisted_order(db, LOW_RISK_ORDER_KEY))
        db.commit()

        after = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))
        assert after.status is OrderLookupStatus.ORDER_NOT_FOUND
        assert after.order is None
        assert after.requested_order_key == LOW_RISK_ORDER_KEY
    finally:
        db.close()


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {},                                    # 缺必要字段
        {"order_key": ""},                     # 空 order id
        {"order_key": "   "},                  # 纯空白
        {"order_key": "order demo 001"},       # 含空格的非法 identifier
        {"order_key": "order-demo-001; DROP TABLE orders"},  # 试图夹带 SQL
        {"order_key": "%"},                    # 试图使用通配符做模糊匹配
        {"order_key": "-order-demo-001"},      # 不符合业务标识符格式
        {"order_key": "o" * 65},               # 超出 business_key 列宽
        {"order_key": None},                   # 类型非法
        {"order_key": 1},                      # 类型非法
    ],
)
def test_invalid_input_fails_schema_validation(invalid_payload):
    """非法输入在 schema 层直接失败，不会进入数据库查询后再模糊失败"""
    with pytest.raises(ValidationError):
        GetOrderRequest(**invalid_payload)


def test_request_rejects_arbitrary_extra_filters():
    """请求契约不接受额外字段，调用方无法夹带任意过滤条件"""
    with pytest.raises(ValidationError):
        GetOrderRequest(order_key=LOW_RISK_ORDER_KEY, status="delivered")


def test_model_text_is_rejected_not_treated_as_success():
    """模型原始文本不得成为查询输入，更不得被包装成 success"""
    db = SessionLocal()
    try:
        result = get_order(db, "订单 order-demo-001 查询成功，商品是无线耳机")

        assert result.status is OrderLookupStatus.INVALID_REQUEST
        assert result.order is None
        assert result.failure_reason is not None
    finally:
        db.close()


def test_success_result_cannot_be_constructed_without_order():
    """即使在应用内部也无法构造没有订单事实却宣称 success 的伪成功结果"""
    with pytest.raises(ValidationError):
        OrderLookupResult(status=OrderLookupStatus.SUCCESS)


def test_failure_result_cannot_carry_order_facts():
    """失败结果不得携带订单事实，避免失败被伪装成半成功状态"""
    facts = OrderFacts(
        order_key=LOW_RISK_ORDER_KEY,
        customer_key="customer-demo-001",
        product_sku="SKU-EARBUD-PRO-01",
        product_name="Flyweave 无线耳机 Pro",
        purchased_at=datetime.utcnow(),
        status=OrderStatus.DELIVERED,
        amount=Decimal("299.00"),
        is_demo_data=True,
    )
    with pytest.raises(ValidationError):
        OrderLookupResult(
            status=OrderLookupStatus.ORDER_NOT_FOUND,
            order=facts,
            requested_order_key=LOW_RISK_ORDER_KEY,
            failure_reason="未找到订单",
        )


def test_failure_result_requires_structured_reason():
    """失败结果必须携带结构化失败原因，空对象无法冒充成功或静默失败"""
    with pytest.raises(ValidationError):
        OrderLookupResult(
            status=OrderLookupStatus.ORDER_NOT_FOUND,
            requested_order_key=LOW_RISK_ORDER_KEY,
        )
