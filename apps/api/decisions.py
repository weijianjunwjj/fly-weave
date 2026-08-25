"""constrained replacement decision 的显式类型契约。

T015 把 T011~T014 产出的四类证据（已验证 intent、政策、订单事实、库存事实）
汇聚成一个 typed、deterministic 的换货资格判定结果。

三个状态显式建模，互不塌陷：

- ``ELIGIBLE``：四类 application-owned 证据齐备，且全部确定性业务规则通过；
- ``BLOCKED``：application-owned 证据明确否定换货（超窗、订单状态、无货）；
- ``AMBIGUOUS``：证据缺失、证据互相冲突，或模型置信度不足以支撑判定。

关键安全性质由 schema 自身保证，而不是靠调用方自觉：

1. ``DecisionEvidence`` 的四个证据字段全部必填，因此不存在"没有引用某类证据
   就得出的决策"；
2. ``ELIGIBLE`` 被 ``model_validator`` 强制要求四类证据同时处于各自的成功状态，
   因此即使在应用内部，也无法构造出"证据缺失/失败却宣称可换货"的伪结果；
3. 模型产出的字段（intent 及其 confidence）只被携带与检查，绝不能覆盖政策、
   订单与库存这些 application-owned 事实。

本模块只定义契约，不包含任何判定逻辑；判定由 ``decision_service`` 依据真实
证据完成。本层只判定"是否有资格换货"，不执行换货、不发起审批、不驱动流程。
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from intents import IntentExtractionStatus
from inventory import InventoryCheckStatus
from models import OrderStatus
from orders import OrderLookupStatus
from policies import PolicyLookupStatus


class ReplacementDecisionStatus(str, Enum):
    """换货判定状态。三态显式并列，ambiguous 绝不被折叠进 eligible。"""

    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    AMBIGUOUS = "ambiguous"


class DecisionReasonCode(str, Enum):
    """判定理由码。每个码唯一对应一条确定性规则，便于测试与 UI 展示。

    理由码而非自由文本才是判定依据的权威表示：``reason`` 只是给人看的说明。
    """

    # --- eligible ---
    POLICY_AND_FACTS_SATISFIED = "policy_and_facts_satisfied"

    # --- blocked：由 application-owned 证据确定性否定 ---
    REPLACEMENT_WINDOW_EXPIRED = "replacement_window_expired"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_NOT_DELIVERED = "order_not_delivered"
    INVENTORY_UNAVAILABLE = "inventory_unavailable"

    # --- ambiguous：证据不足、证据冲突或模型置信度不足 ---
    INTENT_EVIDENCE_MISSING = "intent_evidence_missing"
    POLICY_EVIDENCE_MISSING = "policy_evidence_missing"
    ORDER_EVIDENCE_MISSING = "order_evidence_missing"
    INVENTORY_EVIDENCE_MISSING = "inventory_evidence_missing"
    EVIDENCE_CONFLICT = "evidence_conflict"
    LOW_INTENT_CONFIDENCE = "low_intent_confidence"


ELIGIBLE_REASON_CODES: frozenset[DecisionReasonCode] = frozenset(
    {DecisionReasonCode.POLICY_AND_FACTS_SATISFIED}
)

BLOCKING_REASON_CODES: frozenset[DecisionReasonCode] = frozenset(
    {
        DecisionReasonCode.REPLACEMENT_WINDOW_EXPIRED,
        DecisionReasonCode.ORDER_CANCELLED,
        DecisionReasonCode.ORDER_NOT_DELIVERED,
        DecisionReasonCode.INVENTORY_UNAVAILABLE,
    }
)

AMBIGUOUS_REASON_CODES: frozenset[DecisionReasonCode] = frozenset(
    {
        DecisionReasonCode.INTENT_EVIDENCE_MISSING,
        DecisionReasonCode.POLICY_EVIDENCE_MISSING,
        DecisionReasonCode.ORDER_EVIDENCE_MISSING,
        DecisionReasonCode.INVENTORY_EVIDENCE_MISSING,
        DecisionReasonCode.EVIDENCE_CONFLICT,
        DecisionReasonCode.LOW_INTENT_CONFIDENCE,
    }
)


class IntentEvidence(BaseModel):
    """模型产出的已验证 intent 证据（T011）。

    ``status`` 为 ``None`` 表示压根没有拿到 typed 的 ``IntentExtractionOutcome``
    （例如调用方递来的是模型原始文本），这与"抽取失败"一样不能进入成功路径。

    ``confidence`` 是模型自述的置信度，属于 model-generated 字段：它只能把判定
    降级为 ambiguous，永远不能推翻政策 / 订单 / 库存事实，也不能把 blocked
    抬升为 eligible。
    """

    status: IntentExtractionStatus | None = None
    intent_type: str | None = None
    requested_action: str | None = None
    issue_summary: str | None = None
    confidence: float | None = None


class PolicyEvidence(BaseModel):
    """政策证据（T012）。保留稳定来源标识，使判定可追溯到具体政策来源。"""

    status: PolicyLookupStatus | None = None
    policy_key: str | None = None
    source_reference: str | None = None
    replacement_window_days: int | None = None
    approval_required_above_amount: Decimal | None = None


class OrderEvidence(BaseModel):
    """订单事实证据（T013），字段全部来自持久化订单，不含任何推断值。"""

    status: OrderLookupStatus | None = None
    requested_order_key: str | None = None
    order_key: str | None = None
    customer_key: str | None = None
    product_sku: str | None = None
    order_status: OrderStatus | None = None
    purchased_at: datetime | None = None
    amount: Decimal | None = None


class InventoryEvidence(BaseModel):
    """库存事实证据（T014）。

    ``available_quantity == 0`` 是真实的"无货"事实，与"查无此 SKU"
    （``SKU_NOT_FOUND``）语义不同：前者确定性地阻断换货，后者只是证据缺失。
    """

    status: InventoryCheckStatus | None = None
    requested_sku: str | None = None
    product_sku: str | None = None
    available_quantity: int | None = None
    warehouse: str | None = None


class DecisionEvidence(BaseModel):
    """判定所依据的四类证据。四个字段全部必填。

    这使"决策必须显式引用 intent / policy / order / inventory 证据"成为结构性
    约束：任何 ``ReplacementDecision`` 都必然带着它据以判定的全部证据，包括证据
    缺失这一事实本身。
    """

    intent: IntentEvidence
    policy: PolicyEvidence
    order: OrderEvidence
    inventory: InventoryEvidence


class ReplacementDecision(BaseModel):
    """换货判定结果：状态 + 理由码 + 完整证据引用。

    ``model_validator`` 强制状态与理由码一致，并对 ``ELIGIBLE`` 施加最强约束：
    四类证据必须同时处于成功状态（intent 抽取成功、政策命中、订单查到、库存
    有货）。因此一个"可换货"的结论无法脱离 application-owned 证据而存在。

    ``policy_approval_threshold_exceeded`` 只是由政策阈值与订单金额确定性推导
    出的事实标记，供后续风险闸门（T019）读取；本层不发起、不代表、也不执行
    任何审批流程。
    """

    status: ReplacementDecisionStatus
    reason_code: DecisionReasonCode
    reason: str = Field(min_length=1)
    evidence: DecisionEvidence
    policy_approval_threshold_exceeded: bool = False

    @model_validator(mode="after")
    def _validate_status_matches_reason_and_evidence(self) -> "ReplacementDecision":
        expected_codes = {
            ReplacementDecisionStatus.ELIGIBLE: ELIGIBLE_REASON_CODES,
            ReplacementDecisionStatus.BLOCKED: BLOCKING_REASON_CODES,
            ReplacementDecisionStatus.AMBIGUOUS: AMBIGUOUS_REASON_CODES,
        }[self.status]
        if self.reason_code not in expected_codes:
            raise ValueError(
                f"{self.status.value} 结果不得使用理由码 {self.reason_code.value}"
            )

        if self.status is ReplacementDecisionStatus.ELIGIBLE:
            # 不仅要求成功状态，还要求证据真的携带了具体事实：光有一个 success
            # 枚举而没有政策来源、订单标识或可用数量，不足以支撑"可换货"
            if (
                self.evidence.intent.status is not IntentExtractionStatus.SUCCESS
                or self.evidence.intent.confidence is None
            ):
                raise ValueError(
                    "eligible 结果必须基于成功抽取且携带置信度的 intent 证据"
                )
            if (
                self.evidence.policy.status is not PolicyLookupStatus.SUCCESS
                or self.evidence.policy.policy_key is None
                or self.evidence.policy.replacement_window_days is None
            ):
                raise ValueError("eligible 结果必须基于命中且可追溯的政策证据")
            if (
                self.evidence.order.status is not OrderLookupStatus.SUCCESS
                or self.evidence.order.order_key is None
                or self.evidence.order.purchased_at is None
            ):
                raise ValueError("eligible 结果必须基于查到的真实订单事实")
            if (
                self.evidence.inventory.status is not InventoryCheckStatus.SUCCESS
                or self.evidence.inventory.product_sku is None
                or self.evidence.inventory.available_quantity is None
            ):
                raise ValueError("eligible 结果必须基于有货的真实库存事实")
        return self
