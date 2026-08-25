"""constrained replacement decision 的确定性判定层。

Golden Path 的决策边界：把 T011~T014 的 typed 结果汇聚成一个 typed 的换货
资格判定。

    已验证 intent（T011）
    政策查询结果（T012）      → decide_replacement → ReplacementDecision
    订单事实（T013）
    库存事实（T014）

判定规则完全由应用拥有，且只读取上述四个 typed 结果中的字段；本模块不调用
模型、不接受自由文本、不查询数据库、不改变任何业务状态。同一组证据在同一
时点必然得到同一结论。

本层只回答"是否有资格换货"。它不创建换货单、不写回工单、不发起审批、
不推进流程——这些分别属于 T016 / T017 / T019+。
"""
from datetime import datetime, timedelta

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
    ReplacementIntent,
)
from inventory import InventoryCheckResult, InventoryCheckStatus, InventoryFacts
from models import OrderStatus
from orders import OrderFacts, OrderLookupResult, OrderLookupStatus
from policies import PolicyLookupResult, PolicyLookupStatus, ReplacementPolicyRule

# 模型自述置信度的最低门槛。低于该门槛时判定降级为 ambiguous，交由人工判断，
# 绝不因为"模型很自信"就放行，也绝不因为置信度高就跳过任何确定性规则。
MIN_INTENT_CONFIDENCE = 0.7

# 允许换货的订单状态。质量问题换货以"货已送达"为前提；其余状态各自有明确的
# 确定性结论，不进入模糊地带。
REPLACEABLE_ORDER_STATUSES: frozenset[OrderStatus] = frozenset({OrderStatus.DELIVERED})


def decide_replacement(
    intent_outcome: IntentExtractionOutcome,
    policy_result: PolicyLookupResult,
    order_result: OrderLookupResult,
    inventory_result: InventoryCheckResult,
    now: datetime | None = None,
) -> ReplacementDecision:
    """依据四类真实证据做出换货资格判定。

    参数必须是 T011~T014 各自的 typed 结果；任何非 typed 输入（例如模型原始
    文本）在证据构建阶段就被识别为"证据缺失"，只可能得到 ambiguous，绝不会
    进入 eligible。

    规则求值顺序是确定的，且刻意把 application-owned 证据排在模型置信度之前：

    1. intent / policy / order 证据缺失 → ambiguous；
    2. 订单状态与政策时间窗口的确定性阻断 → blocked；
    3. 库存证据缺失 → ambiguous；证据冲突（库存查的不是订单商品）→ ambiguous；
    4. 真实无货 → blocked；
    5. 模型置信度不足 → ambiguous；
    6. 以上全部通过 → eligible。

    因此模型产出的置信度只能把结论降级为 ambiguous，永远无法推翻或绕过任何
    一条基于政策 / 订单 / 库存事实的阻断规则。
    """
    evaluated_at = now if now is not None else datetime.utcnow()

    intent = _usable_intent(intent_outcome)
    policy_rule = _usable_policy_rule(policy_result)
    order_facts = _usable_order_facts(order_result)
    inventory_facts = _usable_inventory_facts(inventory_result)

    evidence = DecisionEvidence(
        intent=_intent_evidence(intent_outcome),
        policy=_policy_evidence(policy_result),
        order=_order_evidence(order_result),
        inventory=_inventory_evidence(inventory_result),
    )
    threshold_exceeded = _exceeds_policy_approval_threshold(policy_rule, order_facts)

    def decide(
        status: ReplacementDecisionStatus,
        reason_code: DecisionReasonCode,
        reason: str,
    ) -> ReplacementDecision:
        return ReplacementDecision(
            status=status,
            reason_code=reason_code,
            reason=reason,
            evidence=evidence,
            policy_approval_threshold_exceeded=threshold_exceeded,
        )

    def ambiguous(
        reason_code: DecisionReasonCode, reason: str
    ) -> ReplacementDecision:
        return decide(ReplacementDecisionStatus.AMBIGUOUS, reason_code, reason)

    def blocked(reason_code: DecisionReasonCode, reason: str) -> ReplacementDecision:
        return decide(ReplacementDecisionStatus.BLOCKED, reason_code, reason)

    # --- 1. 证据齐备性：缺任何一类都无法判定，只能保持 ambiguous ---
    if intent is None:
        return ambiguous(
            DecisionReasonCode.INTENT_EVIDENCE_MISSING,
            "缺少已验证的结构化 intent，无法判定换货资格",
        )
    if policy_rule is None:
        return ambiguous(
            DecisionReasonCode.POLICY_EVIDENCE_MISSING,
            "缺少可引用的售后政策依据，无法判定换货资格",
        )
    if order_facts is None:
        return ambiguous(
            DecisionReasonCode.ORDER_EVIDENCE_MISSING,
            "缺少真实订单事实，无法判定换货资格",
        )

    # --- 2. 订单状态与政策窗口的确定性阻断 ---
    if order_facts.status is OrderStatus.CANCELLED:
        return blocked(
            DecisionReasonCode.ORDER_CANCELLED,
            f"订单 {order_facts.order_key} 已取消，不具备换货资格",
        )
    if order_facts.status not in REPLACEABLE_ORDER_STATUSES:
        return blocked(
            DecisionReasonCode.ORDER_NOT_DELIVERED,
            (
                f"订单 {order_facts.order_key} 当前状态为 "
                f"{order_facts.status.value}，尚未送达，不具备换货资格"
            ),
        )

    window = timedelta(days=policy_rule.replacement_window_days)
    if evaluated_at - order_facts.purchased_at > window:
        return blocked(
            DecisionReasonCode.REPLACEMENT_WINDOW_EXPIRED,
            (
                f"订单 {order_facts.order_key} 已超出政策 "
                f"{policy_result.source.policy_key} 规定的 "
                f"{policy_rule.replacement_window_days} 天换货窗口"
            ),
        )

    # --- 3. 库存证据缺失 / 证据冲突 ---
    if inventory_facts is None:
        return ambiguous(
            DecisionReasonCode.INVENTORY_EVIDENCE_MISSING,
            "缺少真实库存事实，无法确认换货可用性",
        )
    # 库存查的必须就是订单上的商品；查了别的 SKU 说明证据互相矛盾，此时既不能
    # 据此放行，也不能据此阻断
    if inventory_facts.product_sku != order_facts.product_sku:
        return ambiguous(
            DecisionReasonCode.EVIDENCE_CONFLICT,
            (
                f"库存证据对应 SKU {inventory_facts.product_sku}，"
                f"与订单商品 {order_facts.product_sku} 不一致"
            ),
        )

    # --- 4. 真实无货：数量为零是确定性事实，直接阻断 ---
    if inventory_result.status is InventoryCheckStatus.UNAVAILABLE:
        return blocked(
            DecisionReasonCode.INVENTORY_UNAVAILABLE,
            (
                f"商品 {inventory_facts.product_sku} 在仓库 "
                f"{inventory_facts.warehouse} 可用数量为 0，无法换货"
            ),
        )

    # --- 5. 模型置信度不足：保持 ambiguous，绝不默认放行 ---
    if intent.confidence < MIN_INTENT_CONFIDENCE:
        return ambiguous(
            DecisionReasonCode.LOW_INTENT_CONFIDENCE,
            (
                f"intent 置信度 {intent.confidence} 低于门槛 "
                f"{MIN_INTENT_CONFIDENCE}，需人工判断"
            ),
        )

    # --- 6. 四类证据齐备且全部确定性规则通过 ---
    return decide(
        ReplacementDecisionStatus.ELIGIBLE,
        DecisionReasonCode.POLICY_AND_FACTS_SATISFIED,
        (
            f"订单 {order_facts.order_key} 在政策 "
            f"{policy_result.source.policy_key} 的 "
            f"{policy_rule.replacement_window_days} 天窗口内，"
            f"商品 {inventory_facts.product_sku} 可用数量 "
            f"{inventory_facts.available_quantity}，具备换货资格"
        ),
    )


def _usable_intent(
    intent_outcome: IntentExtractionOutcome,
) -> ReplacementIntent | None:
    """只有 typed 且抽取成功、并真的携带 intent 的结果才算可用证据。

    ``status`` 为 success 却没有 intent 的结果同样视为证据缺失：状态自述不能
    替代事实本身。
    """
    if not isinstance(intent_outcome, IntentExtractionOutcome):
        return None
    if intent_outcome.status is not IntentExtractionStatus.SUCCESS:
        return None
    return intent_outcome.intent


def _usable_policy_rule(
    policy_result: PolicyLookupResult,
) -> ReplacementPolicyRule | None:
    """只有 typed 且命中政策的结果才算可用证据。"""
    if not isinstance(policy_result, PolicyLookupResult):
        return None
    if policy_result.status is not PolicyLookupStatus.SUCCESS:
        return None
    if policy_result.source is None:
        return None
    return policy_result.rule


def _usable_order_facts(order_result: OrderLookupResult) -> OrderFacts | None:
    """只有 typed 且查到真实订单的结果才算可用证据。"""
    if not isinstance(order_result, OrderLookupResult):
        return None
    if order_result.status is not OrderLookupStatus.SUCCESS:
        return None
    return order_result.order


def _usable_inventory_facts(
    inventory_result: InventoryCheckResult,
) -> InventoryFacts | None:
    """有货与无货都携带真实库存事实；查无 SKU / 非法输入则没有可用事实。"""
    if not isinstance(inventory_result, InventoryCheckResult):
        return None
    if inventory_result.status not in (
        InventoryCheckStatus.SUCCESS,
        InventoryCheckStatus.UNAVAILABLE,
    ):
        return None
    return inventory_result.inventory


def _intent_evidence(intent_outcome: IntentExtractionOutcome) -> IntentEvidence:
    """把 intent 抽取结果原样映射为证据引用，不补齐、不推断缺失字段。"""
    if not isinstance(intent_outcome, IntentExtractionOutcome):
        return IntentEvidence()

    intent = intent_outcome.intent
    if intent is None:
        return IntentEvidence(status=intent_outcome.status)

    return IntentEvidence(
        status=intent_outcome.status,
        intent_type=intent.intent_type.value,
        requested_action=intent.requested_action.value,
        issue_summary=intent.issue_summary,
        confidence=intent.confidence,
    )


def _policy_evidence(policy_result: PolicyLookupResult) -> PolicyEvidence:
    """保留政策来源标识与确定性规则，使判定可追溯到具体政策来源。"""
    if not isinstance(policy_result, PolicyLookupResult):
        return PolicyEvidence()

    evidence = PolicyEvidence(status=policy_result.status)
    if policy_result.source is not None:
        evidence.policy_key = policy_result.source.policy_key
        evidence.source_reference = policy_result.source.source_reference
    if policy_result.rule is not None:
        evidence.replacement_window_days = policy_result.rule.replacement_window_days
        evidence.approval_required_above_amount = (
            policy_result.rule.approval_required_above_amount
        )
    return evidence


def _order_evidence(order_result: OrderLookupResult) -> OrderEvidence:
    """订单证据字段全部来自持久化订单事实，失败时只保留被请求的标识。"""
    if not isinstance(order_result, OrderLookupResult):
        return OrderEvidence()

    evidence = OrderEvidence(
        status=order_result.status,
        requested_order_key=order_result.requested_order_key,
    )
    order = order_result.order
    if order is not None:
        evidence.order_key = order.order_key
        evidence.customer_key = order.customer_key
        evidence.product_sku = order.product_sku
        evidence.order_status = order.status
        evidence.purchased_at = order.purchased_at
        evidence.amount = order.amount
    return evidence


def _inventory_evidence(inventory_result: InventoryCheckResult) -> InventoryEvidence:
    """库存证据保留真实可用数量（含 0），无事实时保持空值，不伪造可用性。"""
    if not isinstance(inventory_result, InventoryCheckResult):
        return InventoryEvidence()

    evidence = InventoryEvidence(
        status=inventory_result.status,
        requested_sku=inventory_result.requested_sku,
    )
    inventory = inventory_result.inventory
    if inventory is not None:
        evidence.product_sku = inventory.product_sku
        evidence.available_quantity = inventory.available_quantity
        evidence.warehouse = inventory.warehouse
    return evidence


def _exceeds_policy_approval_threshold(
    policy_rule: ReplacementPolicyRule | None, order_facts: OrderFacts | None
) -> bool:
    """由政策阈值与订单金额确定性推导的事实标记。

    只描述"订单金额是否超过政策阈值"这一事实，供后续风险闸门（T019）读取；
    本层不发起、不代表也不执行任何审批流程。证据不全时保持 False，不猜测。
    """
    if policy_rule is None or order_facts is None:
        return False
    if policy_rule.approval_required_above_amount is None:
        return False
    return order_facts.amount > policy_rule.approval_required_above_amount
