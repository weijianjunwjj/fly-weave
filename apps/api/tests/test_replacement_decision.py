"""T015 constrained replacement decision 的确定性测试。

覆盖任务要求的验收点：
- eligible / blocked / ambiguous 三态各有确定性用例；
- 每个判定都显式引用 intent、policy、order、inventory 四类证据；
- 确定性规则（政策窗口、订单状态、真实无货）能够阻断换货；
- 证据缺失 / 证据冲突 / 模型置信度不足保持 ambiguous，绝不被折叠成 eligible；
- 模型产出的 intent 与置信度无法推翻 application-owned 的政策 / 订单 / 库存事实；
- 同一组证据重复判定结果稳定。

证据全部经由 T011~T014 的真实实现从 seeded 数据产生，而不是手工编造的结构体。
"""
import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from database import SessionLocal
from decision_service import MIN_INTENT_CONFIDENCE, decide_replacement
from decisions import (
    DecisionEvidence,
    DecisionReasonCode,
    IntentEvidence,
    InventoryEvidence,
    OrderEvidence,
    PolicyEvidence,
    ReplacementDecision,
    ReplacementDecisionStatus,
)
from intents import (
    IntentExtractionOutcome,
    IntentExtractionStatus,
    IntentType,
    RequestedAction,
    extract_intent,
)
from inventory import CheckInventoryRequest, InventoryCheckStatus
from inventory_service import check_inventory
from models import AfterSalesPolicy, InventoryItem, OrderStatus
from order_service import get_order
from orders import GetOrderRequest, OrderLookupStatus
from policies import PolicyLookupStatus
from policy_service import lookup_replacement_policy
from seed_data import seed_demo_data

# 低风险场景：5 天前的已送达订单，金额 299，商品有货（数量 12）
LOW_RISK_ORDER_KEY = "order-demo-001"
# 需审批场景：10 天前的已送达订单，金额 1299，超过政策 500 的审批阈值
HIGH_VALUE_ORDER_KEY = "order-demo-002"
# 拒绝场景：60 天前的订单，超出 30 天换货窗口，且商品无货
OUT_OF_WINDOW_ORDER_KEY = "order-demo-003"

AVAILABLE_SKU = "SKU-EARBUD-PRO-01"
UNAVAILABLE_SKU = "SKU-HEADSET-X-02"

POLICY_KEY = "policy-replacement-standard"
POLICY_SOURCE_REFERENCE = "policy-doc://after-sales/v1#replacement"


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据，保证证据来源确定且互不污染。

    个别测试会删除政策行或把库存清零以构造真实的失败证据，随后由 fixture
    在下一次播种时重建。
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


def _intent_outcome(confidence: float = 0.95):
    """经由 T011 的 validation boundary 产生一条真实的已验证 intent。"""
    raw = json.dumps(
        {
            "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT.value,
            "issue_summary": "右耳耳机无声，疑似质量问题",
            "requested_action": RequestedAction.REPLACEMENT.value,
            "confidence": confidence,
        }
    )
    outcome = extract_intent(raw)
    assert outcome.status is IntentExtractionStatus.SUCCESS
    return outcome


def _evidence_from_seed(db, order_key: str, sku: str, confidence: float = 0.95):
    """用 T011~T014 的真实实现，从 seeded 数据取出四类证据。"""
    intent = _intent_outcome(confidence)
    return (
        intent,
        lookup_replacement_policy(db, intent.intent),
        get_order(db, GetOrderRequest(order_key=order_key)),
        check_inventory(db, CheckInventoryRequest(product_sku=sku)),
    )


def _assert_references_all_evidence(decision: ReplacementDecision) -> None:
    """任何判定都必须显式引用四类证据，而不是只给出一个结论。"""
    assert decision.evidence.intent is not None
    assert decision.evidence.policy is not None
    assert decision.evidence.order is not None
    assert decision.evidence.inventory is not None


# --------------------------------------------------------------------------
# eligible
# --------------------------------------------------------------------------


def test_low_risk_case_is_eligible_and_references_all_evidence():
    """窗口内、已送达、有货的低风险订单判定为 eligible，并引用全部真实证据"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU
        )
        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.ELIGIBLE
        assert decision.reason_code is DecisionReasonCode.POLICY_AND_FACTS_SATISFIED
        _assert_references_all_evidence(decision)

        # intent 证据：来自 T011 的已验证结构化 intent
        assert decision.evidence.intent.status is IntentExtractionStatus.SUCCESS
        assert (
            decision.evidence.intent.intent_type
            == IntentType.QUALITY_ISSUE_REPLACEMENT.value
        )
        assert (
            decision.evidence.intent.requested_action
            == RequestedAction.REPLACEMENT.value
        )
        assert decision.evidence.intent.confidence == 0.95

        # policy 证据：保留稳定来源标识，可追溯到具体政策
        assert decision.evidence.policy.status is PolicyLookupStatus.SUCCESS
        assert decision.evidence.policy.policy_key == POLICY_KEY
        assert decision.evidence.policy.source_reference == POLICY_SOURCE_REFERENCE
        assert decision.evidence.policy.replacement_window_days == 30

        # order 证据：来自持久化订单事实
        assert decision.evidence.order.status is OrderLookupStatus.SUCCESS
        assert decision.evidence.order.order_key == LOW_RISK_ORDER_KEY
        assert decision.evidence.order.order_status is OrderStatus.DELIVERED
        assert decision.evidence.order.product_sku == AVAILABLE_SKU
        assert decision.evidence.order.customer_key == "customer-demo-001"

        # inventory 证据：来自持久化库存事实
        assert decision.evidence.inventory.status is InventoryCheckStatus.SUCCESS
        assert decision.evidence.inventory.product_sku == AVAILABLE_SKU
        assert decision.evidence.inventory.available_quantity == 12
        assert decision.evidence.inventory.warehouse == "WH-EAST-01"

        # 低风险订单金额未超过政策审批阈值
        assert decision.policy_approval_threshold_exceeded is False
    finally:
        db.close()


def test_high_value_order_marks_policy_threshold_without_starting_approval():
    """高金额订单据实标记超过政策阈值，本层仍只做资格判定，不发起审批"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, HIGH_VALUE_ORDER_KEY, AVAILABLE_SKU
        )
        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.ELIGIBLE
        assert decision.policy_approval_threshold_exceeded is True
        # 阈值标记直接来自政策证据与订单金额，二者都被显式引用
        assert decision.evidence.policy.approval_required_above_amount is not None
        assert (
            decision.evidence.order.amount
            > decision.evidence.policy.approval_required_above_amount
        )
        # 三态之外不存在其它状态，本层不引入 approval 状态
        assert decision.status in tuple(ReplacementDecisionStatus)
    finally:
        db.close()


# --------------------------------------------------------------------------
# blocked
# --------------------------------------------------------------------------


def test_out_of_window_order_is_blocked_by_policy_rule():
    """超出政策换货窗口的订单被确定性阻断，理由码明确指向窗口规则"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, OUT_OF_WINDOW_ORDER_KEY, UNAVAILABLE_SKU
        )
        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.BLOCKED
        assert decision.reason_code is DecisionReasonCode.REPLACEMENT_WINDOW_EXPIRED
        _assert_references_all_evidence(decision)

        # 阻断结论由政策规则与订单购买时间共同确定，二者都在证据中可查
        assert decision.evidence.policy.replacement_window_days == 30
        assert decision.evidence.order.order_key == OUT_OF_WINDOW_ORDER_KEY
        window = timedelta(days=decision.evidence.policy.replacement_window_days)
        assert datetime.utcnow() - decision.evidence.order.purchased_at > window
    finally:
        db.close()


def test_unavailable_inventory_blocks_replacement():
    """窗口内订单但商品真实无货时被确定性阻断，且不伪造可用性"""
    db = SessionLocal()
    try:
        # 把低风险场景的商品库存清零，制造真实的无货事实
        item = (
            db.query(InventoryItem)
            .filter(InventoryItem.product_sku == AVAILABLE_SKU)
            .one()
        )
        item.available_quantity = 0
        db.commit()

        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU
        )
        assert inventory.status is InventoryCheckStatus.UNAVAILABLE

        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.BLOCKED
        assert decision.reason_code is DecisionReasonCode.INVENTORY_UNAVAILABLE
        _assert_references_all_evidence(decision)
        assert decision.evidence.inventory.status is InventoryCheckStatus.UNAVAILABLE
        assert decision.evidence.inventory.available_quantity == 0
        assert decision.evidence.order.order_key == LOW_RISK_ORDER_KEY
    finally:
        db.close()


def test_cancelled_order_is_blocked():
    """已取消订单被确定性阻断，理由码与订单状态一致"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU
        )
        cancelled = order.model_copy(deep=True)
        cancelled.order.status = OrderStatus.CANCELLED

        decision = decide_replacement(intent, policy, cancelled, inventory)

        assert decision.status is ReplacementDecisionStatus.BLOCKED
        assert decision.reason_code is DecisionReasonCode.ORDER_CANCELLED
        assert decision.evidence.order.order_status is OrderStatus.CANCELLED
    finally:
        db.close()


def test_undelivered_order_is_blocked():
    """尚未送达的订单被确定性阻断，而不是被当作可换货"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU
        )
        shipped = order.model_copy(deep=True)
        shipped.order.status = OrderStatus.SHIPPED

        decision = decide_replacement(intent, policy, shipped, inventory)

        assert decision.status is ReplacementDecisionStatus.BLOCKED
        assert decision.reason_code is DecisionReasonCode.ORDER_NOT_DELIVERED
        assert decision.evidence.order.order_status is OrderStatus.SHIPPED
    finally:
        db.close()


# --------------------------------------------------------------------------
# ambiguous
# --------------------------------------------------------------------------


def test_low_confidence_intent_stays_ambiguous_not_eligible():
    """事实全部满足但模型置信度不足时保持 ambiguous，不默认放行"""
    db = SessionLocal()
    try:
        low_confidence = MIN_INTENT_CONFIDENCE - 0.3
        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU, confidence=low_confidence
        )
        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
        assert decision.reason_code is DecisionReasonCode.LOW_INTENT_CONFIDENCE
        _assert_references_all_evidence(decision)
        assert decision.evidence.intent.confidence == low_confidence
        # 事实证据本身是齐备且正向的，ambiguous 完全来自置信度不足
        assert decision.evidence.order.status is OrderLookupStatus.SUCCESS
        assert decision.evidence.inventory.status is InventoryCheckStatus.SUCCESS
    finally:
        db.close()


def test_missing_order_evidence_is_ambiguous():
    """查不到订单时证据不足，判定保持 ambiguous 而非 eligible / blocked"""
    db = SessionLocal()
    try:
        intent = _intent_outcome()
        policy = lookup_replacement_policy(db, intent.intent)
        order = get_order(db, GetOrderRequest(order_key="order-does-not-exist"))
        inventory = check_inventory(
            db, CheckInventoryRequest(product_sku=AVAILABLE_SKU)
        )
        assert order.status is OrderLookupStatus.ORDER_NOT_FOUND

        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
        assert decision.reason_code is DecisionReasonCode.ORDER_EVIDENCE_MISSING
        _assert_references_all_evidence(decision)
        assert decision.evidence.order.status is OrderLookupStatus.ORDER_NOT_FOUND
        assert decision.evidence.order.requested_order_key == "order-does-not-exist"
        assert decision.evidence.order.order_key is None
    finally:
        db.close()


def test_missing_policy_evidence_is_ambiguous():
    """政策查不到时缺少判定依据，保持 ambiguous"""
    db = SessionLocal()
    try:
        policy_row = (
            db.query(AfterSalesPolicy)
            .filter(AfterSalesPolicy.business_key == POLICY_KEY)
            .one()
        )
        db.delete(policy_row)
        db.commit()

        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU
        )
        assert policy.status is PolicyLookupStatus.POLICY_NOT_FOUND

        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
        assert decision.reason_code is DecisionReasonCode.POLICY_EVIDENCE_MISSING
        assert decision.evidence.policy.status is PolicyLookupStatus.POLICY_NOT_FOUND
        assert decision.evidence.policy.replacement_window_days is None
    finally:
        db.close()


def test_failed_intent_extraction_is_ambiguous():
    """intent 抽取失败时没有可用意图证据，保持 ambiguous"""
    db = SessionLocal()
    try:
        failed_intent = extract_intent("模型说这个可以换货")
        assert failed_intent.status is not IntentExtractionStatus.SUCCESS

        policy = lookup_replacement_policy(db, _intent_outcome().intent)
        order = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))
        inventory = check_inventory(
            db, CheckInventoryRequest(product_sku=AVAILABLE_SKU)
        )

        decision = decide_replacement(failed_intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
        assert decision.reason_code is DecisionReasonCode.INTENT_EVIDENCE_MISSING
        assert decision.evidence.intent.status is failed_intent.status
        assert decision.evidence.intent.confidence is None
    finally:
        db.close()


def test_success_status_without_intent_payload_is_ambiguous():
    """抽取结果自称成功却没有携带 intent 时仍属证据缺失，不得进入 eligible"""
    db = SessionLocal()
    try:
        hollow_intent = IntentExtractionOutcome(status=IntentExtractionStatus.SUCCESS)
        assert hollow_intent.intent is None

        policy = lookup_replacement_policy(db, _intent_outcome().intent)
        order = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))
        inventory = check_inventory(
            db, CheckInventoryRequest(product_sku=AVAILABLE_SKU)
        )

        decision = decide_replacement(hollow_intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
        assert decision.reason_code is DecisionReasonCode.INTENT_EVIDENCE_MISSING
        # 状态自述不能替代事实：置信度等实际字段仍然为空
        assert decision.evidence.intent.status is IntentExtractionStatus.SUCCESS
        assert decision.evidence.intent.confidence is None
        assert decision.evidence.intent.intent_type is None
    finally:
        db.close()


def test_missing_inventory_evidence_is_ambiguous():
    """查无此 SKU 属于证据缺失，与真实无货不同，保持 ambiguous 而非 blocked"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, "SKU-DOES-NOT-EXIST-99"
        )
        assert inventory.status is InventoryCheckStatus.SKU_NOT_FOUND

        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
        assert decision.reason_code is DecisionReasonCode.INVENTORY_EVIDENCE_MISSING
        assert decision.evidence.inventory.status is InventoryCheckStatus.SKU_NOT_FOUND
        assert decision.evidence.inventory.available_quantity is None
    finally:
        db.close()


def test_conflicting_sku_evidence_is_ambiguous():
    """库存证据对应的商品与订单商品不一致时，证据冲突保持 ambiguous"""
    db = SessionLocal()
    try:
        # 订单是耳机（有货），却拿了头戴耳机（无货）的库存事实
        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, UNAVAILABLE_SKU
        )
        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
        assert decision.reason_code is DecisionReasonCode.EVIDENCE_CONFLICT
        assert decision.evidence.order.product_sku == AVAILABLE_SKU
        assert decision.evidence.inventory.product_sku == UNAVAILABLE_SKU
    finally:
        db.close()


# --------------------------------------------------------------------------
# 模型产出不得覆盖 application-owned 证据
# --------------------------------------------------------------------------


def test_high_confidence_intent_cannot_override_blocking_facts():
    """模型再自信也无法推翻政策窗口这一 application-owned 事实"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, OUT_OF_WINDOW_ORDER_KEY, UNAVAILABLE_SKU, confidence=1.0
        )
        decision = decide_replacement(intent, policy, order, inventory)

        assert decision.status is ReplacementDecisionStatus.BLOCKED
        assert decision.reason_code is DecisionReasonCode.REPLACEMENT_WINDOW_EXPIRED
        assert decision.evidence.intent.confidence == 1.0
    finally:
        db.close()


def test_model_text_evidence_is_rejected_not_treated_as_eligible():
    """模型原始文本不是证据：全部传入文本只能得到 ambiguous"""
    decision = decide_replacement(
        "客户要求换货",
        "政策允许换货",
        "订单在 30 天窗口内",
        "库存充足，有货",
    )

    assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
    assert decision.reason_code is DecisionReasonCode.INTENT_EVIDENCE_MISSING
    assert decision.evidence.intent.status is None
    assert decision.evidence.policy.status is None
    assert decision.evidence.order.status is None
    assert decision.evidence.inventory.status is None


def test_model_supplied_facts_cannot_fill_missing_application_evidence():
    """已验证 intent 存在，但缺少订单事实时不得由模型文本补齐"""
    db = SessionLocal()
    try:
        intent = _intent_outcome()
        policy = lookup_replacement_policy(db, intent.intent)
        inventory = check_inventory(
            db, CheckInventoryRequest(product_sku=AVAILABLE_SKU)
        )

        decision = decide_replacement(
            intent, policy, "订单 order-demo-001 已送达，5 天前购买", inventory
        )

        assert decision.status is ReplacementDecisionStatus.AMBIGUOUS
        assert decision.reason_code is DecisionReasonCode.ORDER_EVIDENCE_MISSING
        assert decision.evidence.order.status is None
        assert decision.evidence.order.order_key is None
    finally:
        db.close()


# --------------------------------------------------------------------------
# 契约层保证
# --------------------------------------------------------------------------


def _evidence_with(**overrides) -> DecisionEvidence:
    """构造一份四类证据齐备且全部成功的基线证据，便于逐项替换做反面用例。"""
    payload = {
        "intent": IntentEvidence(
            status=IntentExtractionStatus.SUCCESS,
            intent_type=IntentType.QUALITY_ISSUE_REPLACEMENT.value,
            requested_action=RequestedAction.REPLACEMENT.value,
            issue_summary="右耳耳机无声，疑似质量问题",
            confidence=0.9,
        ),
        "policy": PolicyEvidence(
            status=PolicyLookupStatus.SUCCESS,
            policy_key=POLICY_KEY,
            source_reference=POLICY_SOURCE_REFERENCE,
            replacement_window_days=30,
        ),
        "order": OrderEvidence(
            status=OrderLookupStatus.SUCCESS,
            requested_order_key=LOW_RISK_ORDER_KEY,
            order_key=LOW_RISK_ORDER_KEY,
            product_sku=AVAILABLE_SKU,
            order_status=OrderStatus.DELIVERED,
            # 契约层用例不参与时间窗口求值，使用固定时间保持确定性
            purchased_at=datetime(2026, 1, 1),
        ),
        "inventory": InventoryEvidence(
            status=InventoryCheckStatus.SUCCESS,
            requested_sku=AVAILABLE_SKU,
            product_sku=AVAILABLE_SKU,
            available_quantity=12,
            warehouse="WH-EAST-01",
        ),
    }
    payload.update(overrides)
    return DecisionEvidence(**payload)


def test_eligible_can_be_constructed_with_complete_evidence():
    """四类证据齐备且成功时 eligible 可以构造：约束针对证据，而非一刀切禁止"""
    decision = ReplacementDecision(
        status=ReplacementDecisionStatus.ELIGIBLE,
        reason_code=DecisionReasonCode.POLICY_AND_FACTS_SATISFIED,
        reason="四类证据齐备且确定性规则全部通过",
        evidence=_evidence_with(),
    )

    assert decision.status is ReplacementDecisionStatus.ELIGIBLE
    assert decision.evidence.policy.policy_key == POLICY_KEY
    assert decision.evidence.order.order_key == LOW_RISK_ORDER_KEY
    assert decision.evidence.inventory.available_quantity == 12


def test_eligible_requires_evidence_identifiers_not_only_success_status():
    """只有成功状态、却没有政策来源与订单标识时，仍无法构造 eligible"""
    with pytest.raises(ValidationError):
        ReplacementDecision(
            status=ReplacementDecisionStatus.ELIGIBLE,
            reason_code=DecisionReasonCode.POLICY_AND_FACTS_SATISFIED,
            reason="只有状态没有证据实体",
            evidence=_evidence_with(
                policy=PolicyEvidence(status=PolicyLookupStatus.SUCCESS),
                order=OrderEvidence(status=OrderLookupStatus.SUCCESS),
            ),
        )


def test_eligible_requires_intent_confidence_evidence():
    """intent 自称抽取成功却没有携带置信度时，无法构造 eligible"""
    with pytest.raises(ValidationError):
        ReplacementDecision(
            status=ReplacementDecisionStatus.ELIGIBLE,
            reason_code=DecisionReasonCode.POLICY_AND_FACTS_SATISFIED,
            reason="intent 证据不完整",
            evidence=_evidence_with(
                intent=IntentEvidence(status=IntentExtractionStatus.SUCCESS)
            ),
        )


def test_eligible_cannot_be_constructed_without_successful_evidence():
    """即使在应用内部也无法构造"证据缺失却宣称可换货"的伪结果"""
    with pytest.raises(ValidationError):
        ReplacementDecision(
            status=ReplacementDecisionStatus.ELIGIBLE,
            reason_code=DecisionReasonCode.POLICY_AND_FACTS_SATISFIED,
            reason="伪造的可换货结论",
            evidence=_evidence_with(
                inventory=InventoryEvidence(status=InventoryCheckStatus.UNAVAILABLE)
            ),
        )


def test_eligible_cannot_be_constructed_without_policy_evidence():
    """缺少政策证据时无法构造 eligible"""
    with pytest.raises(ValidationError):
        ReplacementDecision(
            status=ReplacementDecisionStatus.ELIGIBLE,
            reason_code=DecisionReasonCode.POLICY_AND_FACTS_SATISFIED,
            reason="伪造的可换货结论",
            evidence=_evidence_with(policy=PolicyEvidence()),
        )


def test_status_and_reason_code_must_agree():
    """阻断理由码不得被挂在 eligible 上，ambiguous 理由码同样不得伪装成 eligible"""
    with pytest.raises(ValidationError):
        ReplacementDecision(
            status=ReplacementDecisionStatus.ELIGIBLE,
            reason_code=DecisionReasonCode.INVENTORY_UNAVAILABLE,
            reason="状态与理由码不一致",
            evidence=_evidence_with(),
        )

    with pytest.raises(ValidationError):
        ReplacementDecision(
            status=ReplacementDecisionStatus.BLOCKED,
            reason_code=DecisionReasonCode.LOW_INTENT_CONFIDENCE,
            reason="状态与理由码不一致",
            evidence=_evidence_with(),
        )


def test_decision_requires_all_four_evidence_fields():
    """决策结构上必须携带四类证据，缺一不可"""
    with pytest.raises(ValidationError):
        DecisionEvidence(
            intent=IntentEvidence(),
            policy=PolicyEvidence(),
            order=OrderEvidence(),
        )


# --------------------------------------------------------------------------
# 确定性
# --------------------------------------------------------------------------


def test_repeated_decision_is_stable():
    """同一组证据重复判定得到完全相同的结果"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU
        )
        now = datetime.utcnow()

        first = decide_replacement(intent, policy, order, inventory, now=now)
        second = decide_replacement(intent, policy, order, inventory, now=now)
        third = decide_replacement(intent, policy, order, inventory, now=now)

        assert first == second == third
    finally:
        db.close()


def test_window_rule_is_evaluated_against_supplied_time():
    """窗口规则以传入时点确定性求值：同一订单跨过窗口边界后由 eligible 变 blocked"""
    db = SessionLocal()
    try:
        intent, policy, order, inventory = _evidence_from_seed(
            db, LOW_RISK_ORDER_KEY, AVAILABLE_SKU
        )
        purchased_at = order.order.purchased_at
        window_days = policy.rule.replacement_window_days

        inside = decide_replacement(
            intent,
            policy,
            order,
            inventory,
            now=purchased_at + timedelta(days=window_days),
        )
        outside = decide_replacement(
            intent,
            policy,
            order,
            inventory,
            now=purchased_at + timedelta(days=window_days, seconds=1),
        )

        assert inside.status is ReplacementDecisionStatus.ELIGIBLE
        assert outside.status is ReplacementDecisionStatus.BLOCKED
        assert outside.reason_code is DecisionReasonCode.REPLACEMENT_WINDOW_EXPIRED
    finally:
        db.close()
