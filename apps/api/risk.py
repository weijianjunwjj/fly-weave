"""受保护业务动作的确定性风险门禁契约（T019）。

Golden Path 上唯一真正改变业务状态的动作是 ``create_replacement``（T016）。
T019 在它真正写入换货单之前加一道闸门：命中风险规则的执行必须停下来等待人工
审批，而不是继续执行。

本模块只定义契约，不包含任何规则求值逻辑；规则由 ``risk_service`` 依据既有的
结构化业务事实确定性求值。

三条不可让步的性质由 schema 自身保证，而不是靠调用方自觉：

1. **风险判断是结构化的，不是一句文本。** ``RiskAssessment`` 同时携带稳定的
   风险级别 ``level`` 与规则标识 ``rule_code``，以及一段可直接展示给用户的
   ``reason``。UI 不需要、也不应该重新推导规则。
2. **"需要审批"不能与风险级别脱节。** ``model_validator`` 强制
   ``requires_approval``、``level`` 与 ``rule_code`` 三者一致，因此
   "命中了高风险规则却标记为无需审批"在应用内部也构造不出来。
3. **命中必须有据可查。** 需要审批的判断必须携带规则求值所依据的结构化业务
   输入（订单标识与金额、政策标识与阈值），因此"要求审批却说不出为什么"
   同样无法构造。

不在本任务范围内：``ApprovalRequest`` 的持久化建模、approve / reject 流程。
本模块不定义审批对象，也不定义任何审批结果状态 —— 风险判断只回答"这次执行
是否必须先经过人工审批"，不回答"审批结果是什么"。
"""
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProtectedAction(str, Enum):
    """受风险门禁保护的业务动作。

    当前只有 ``CREATE_REPLACEMENT`` 一个真实取值：它是现有 domain 中唯一会
    真正改变业务状态的动作。其余动作在当前 domain 中尚不存在真实执行路径，
    因此不预先编造。
    """

    CREATE_REPLACEMENT = "create_replacement"


class RiskLevel(str, Enum):
    """风险级别。

    - ``LOW``：没有任何风险规则命中，受保护动作可以继续执行；
    - ``HIGH``：命中了确定性风险规则，受保护动作必须先经过人工审批。

    当前只建模这两级：现有政策只给出了"超过金额阈值需人工审批"这一条确定性
    分界线，中间级别没有任何真实规则可以产生它。
    """

    LOW = "low"
    HIGH = "high"


class RiskRuleCode(str, Enum):
    """风险规则标识。每个码唯一对应一条确定性规则，便于测试与 UI 展示。

    规则码而非自由文本才是风险判断依据的权威表示：``reason`` 只是给人看的说明。

    - ``NO_RULE_TRIGGERED``：没有任何风险规则命中；
    - ``ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD``：订单金额超过售后政策显式规定
      的人工审批金额阈值。两个取值都来自持久化的结构化业务事实
      （``Order.amount`` 与 ``AfterSalesPolicy.approval_required_above_amount``），
      比较结果不依赖模型判断、随机数或当前时间。
    """

    NO_RULE_TRIGGERED = "no_rule_triggered"
    ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD = "order_amount_above_approval_threshold"


# 命中即要求人工审批的规则码。新增规则时在此登记，使"哪些规则会拦截执行"
# 是一份可枚举、可测试的清单，而不是散落在各处的 if 判断。
APPROVAL_REQUIRED_RULE_CODES: frozenset[RiskRuleCode] = frozenset(
    {RiskRuleCode.ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD}
)


class RiskAssessment(BaseModel):
    """一次受保护动作的风险判断结果。

    ``reason`` 是完整、可直接展示的中文句子，UI 原样呈现即可；``level`` 与
    ``rule_code`` 是稳定标识，供前端做样式区分与后端做测试断言。
    """

    # 判断结果由后端产生并原样传给 UI，不接受调用方夹带额外字段
    model_config = ConfigDict(extra="forbid")

    action: ProtectedAction
    level: RiskLevel
    rule_code: RiskRuleCode
    requires_approval: bool
    reason: str = Field(min_length=1)

    # --- 规则求值所依据的结构化业务输入 ---
    # 全部来自既有持久化事实。命中风险时必填，使"要求审批却说不出依据"无法构造。
    order_key: str | None = None
    order_amount: Decimal | None = None
    approval_threshold_amount: Decimal | None = None
    policy_key: str | None = None

    @model_validator(mode="after")
    def _validate_level_rule_and_evidence_agree(self) -> "RiskAssessment":
        triggered = self.rule_code in APPROVAL_REQUIRED_RULE_CODES

        if triggered != self.requires_approval:
            raise ValueError(
                f"规则 {self.rule_code.value} 与 requires_approval="
                f"{self.requires_approval} 不一致"
            )
        expected_level = RiskLevel.HIGH if triggered else RiskLevel.LOW
        if self.level is not expected_level:
            raise ValueError(
                f"规则 {self.rule_code.value} 的风险级别必须是 "
                f"{expected_level.value}"
            )

        if self.requires_approval:
            # 命中必须有据可查：说得出是哪张订单、哪条政策、哪个阈值被越过
            missing = [
                name
                for name, value in (
                    ("order_key", self.order_key),
                    ("order_amount", self.order_amount),
                    ("approval_threshold_amount", self.approval_threshold_amount),
                    ("policy_key", self.policy_key),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    "需要人工审批的风险判断必须携带完整的规则依据，缺少: "
                    + ", ".join(missing)
                )
        return self
