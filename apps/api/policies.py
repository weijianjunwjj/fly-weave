"""policy lookup 的显式类型契约。

T012 为 seeded quality issue / replacement 场景提供一个 deterministic 的
售后政策查询能力。查询结果必须是带明确 source identity / metadata 的
typed schema，而不是一段无法追踪来源的裸字符串。

本模块只定义契约，不包含任何检索、embedding 或模型调用逻辑。查询本身由
``policy_service`` 依据 application-owned 映射完成，LLM 只负责提供已验证的
structured intent，不拥有政策真实性判定权。
"""
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from intents import IntentType, RequestedAction


class PolicyLookupStatus(str, Enum):
    """policy lookup 结果状态。"""

    SUCCESS = "success"
    POLICY_NOT_FOUND = "policy_not_found"
    UNSUPPORTED_QUERY = "unsupported_query"


class PolicySource(BaseModel):
    """政策的稳定来源标识与必要 metadata。

    ``source_reference`` 是可被 decision layer 直接引用的稳定来源定位符，
    使最终推荐能够追溯到具体政策来源，而不是一段匿名文本。
    """

    policy_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    is_demo_data: bool


class ReplacementPolicyRule(BaseModel):
    """换货政策的确定性业务规则。

    字段来自现有 ``AfterSalesPolicy`` domain model，供后续 T015 决策层做
    确定性约束引用（时间窗口、审批阈值）。
    """

    replacement_window_days: int
    approval_required_above_amount: Decimal | None = None


class PolicyLookupResult(BaseModel):
    """查询结果：成功时携带 source 与 rule，失败时只携带结构化失败原因。

    ``model_validator`` 强制成功与失败路径互斥：SUCCESS 必须同时拥有
    source 与 rule；非 SUCCESS 一律不得携带二者。这样即使在应用内部也无法
    构造出"无来源、无规则却宣称成功"的伪成功结果。
    """

    status: PolicyLookupStatus
    source: PolicySource | None = None
    rule: ReplacementPolicyRule | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _validate_success_requires_source_and_rule(self) -> "PolicyLookupResult":
        if self.status is PolicyLookupStatus.SUCCESS:
            if self.source is None or self.rule is None:
                raise ValueError(
                    "success 结果必须同时携带 policy source 与 replacement rule"
                )
        else:
            if self.source is not None or self.rule is not None:
                raise ValueError(
                    "失败结果不得携带 policy source 或 replacement rule"
                )
        return self


# deterministic、application-owned 的查询键映射：由已验证的 structured intent
# 精确映射到政策 business_key。这是 baseline，不使用 embeddings / 向量检索 /
# 语义搜索 / RAG。后续扩展其它 intent 时在此登记确定性映射，而不是让模型猜测。
SUPPORTED_POLICY_KEYS: dict[tuple[IntentType, RequestedAction], str] = {
    (IntentType.QUALITY_ISSUE_REPLACEMENT, RequestedAction.REPLACEMENT): (
        "policy-replacement-standard"
    ),
}
