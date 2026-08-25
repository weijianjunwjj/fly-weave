"""create_replacement Tool 的显式类型契约。

T016 是 Golden Path 上第一个真正改变业务状态的 Tool：前面的 get_order /
check_inventory / policy lookup 都只读取事实，而 create_replacement 会持久化
一张真实的换货单。

因此这里的契约比只读 Tool 更严格：

- 输入只能是已验证的 ``CreateReplacementRequest``，且不含任何"是否成功"、
  "是否有资格"之类的自述字段——资格由 T015 的 ``ReplacementDecision`` 提供，
  执行前置条件由 ``replacement_service`` 对照持久化状态逐条校验；
- 输出的 ``CREATED`` 状态必须携带一条真实换货单的标识与三条业务关联
  （order / ticket / run），因此"宣称创建成功却没有换货单"在契约层就无法构造；
- 重复执行不是成功，也不是普通失败：它有独立的 ``DUPLICATE`` 状态，并携带已
  存在的换货单标识，使调用方能诚实地区分"这次创建了"与"之前已经创建过"。

本模块只定义契约，不包含任何校验或写入逻辑。真实执行由 ``replacement_service``
在数据库事务中完成。
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import ReplacementStatus


class CreateReplacementStatus(str, Enum):
    """create_replacement 结果状态。

    成功只有一种，失败按"为什么不能执行"显式分列，绝不折叠成一个笼统的 error：

    - ``CREATED``：全部前置条件通过，换货单已真实写入数据库；
    - ``DUPLICATE``：该订单 / 该次 Run 已经执行过换货，本次被安全拒绝，
      不产生第二张换货单；
    - ``NOT_ELIGIBLE``：T015 的判定不是 eligible（blocked / ambiguous），
      受保护的业务变更不得执行；
    - ``EVIDENCE_MISMATCH``：判定所依据的订单 / 商品与本次请求不是同一个，
      即拿着另一个案子的资格来执行本案；
    - ``ORDER_NOT_FOUND``：请求的订单在持久化状态中不存在；
    - ``ORDER_NOT_REPLACEABLE``：执行时刻订单的真实状态不允许换货；
    - ``INVENTORY_UNAVAILABLE``：执行时刻库存的真实可用数量不足；
    - ``RUN_LINKAGE_INVALID``：Agent Run 与该订单 / 工单不是同一个业务上下文；
    - ``INVALID_REQUEST``：输入不是已验证的 typed 请求（例如模型原始文本）。
    """

    CREATED = "created"
    DUPLICATE = "duplicate"
    NOT_ELIGIBLE = "not_eligible"
    EVIDENCE_MISMATCH = "evidence_mismatch"
    ORDER_NOT_FOUND = "order_not_found"
    ORDER_NOT_REPLACEABLE = "order_not_replaceable"
    INVENTORY_UNAVAILABLE = "inventory_unavailable"
    RUN_LINKAGE_INVALID = "run_linkage_invalid"
    INVALID_REQUEST = "invalid_request"


class CreateReplacementRequest(BaseModel):
    """create_replacement 的输入契约。

    字段集合与 plan 中约定的 create_replacement 输入一致（订单、商品、原因），
    并沿用 get_order / check_inventory 已确立的标识约定：对外只用稳定的业务
    标识符，不暴露自增主键。

    ``extra="forbid"`` 使调用方无法夹带 ``status``、``force`` 之类的字段来试探
    出绕过校验的旁路；请求里不存在任何可以自述"我有资格"的字段。

    plan 中提到的 approval reference 属于 T019 / T020 的审批闸门，当前 domain
    尚不存在审批对象，因此这里不预先编造该字段。
    """

    model_config = ConfigDict(extra="forbid")

    # 与 Order.business_key 的列宽与命名约定保持一致
    order_key: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    # 与 InventoryItem.product_sku 的列宽与命名约定保持一致
    product_sku: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    # 换货原因，实际取值来自已验证 intent 的 issue_summary；纯空白在此直接失败
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _reject_blank_reason(self) -> "CreateReplacementRequest":
        if not self.reason.strip():
            raise ValueError("换货原因不得为空白")
        return self


class ReplacementRecord(BaseModel):
    """已持久化换货单的类型化视图。

    每个字段都直接来自数据库中真实存在的 ``ReplacementOrder`` 行，包括三条
    业务关联的稳定标识。这里不存在任何由调用方或模型填写的字段，因此拿到一个
    ``ReplacementRecord`` 就等价于"数据库里确实有这张换货单"。
    """

    replacement_key: str
    status: ReplacementStatus
    order_key: str
    ticket_key: str
    agent_run_key: str
    product_sku: str
    reason: str
    is_demo_data: bool
    created_at: datetime


class CreateReplacementResult(BaseModel):
    """create_replacement 结果：成功携带真实换货单，失败只携带结构化原因。

    ``model_validator`` 强制三条互斥规则，使伪成功在应用内部也无法构造：

    1. ``CREATED`` 必须携带 ``replacement``，且不得携带失败原因；
    2. 任何非 ``CREATED`` 一律不得携带 ``replacement``，且必须给出失败原因——
       失败无法伪装成"半成功"；
    3. ``DUPLICATE`` 必须指出已存在的换货单标识，其余状态不得携带该字段，
       因此"重复"永远是有据可查的事实，而不是一句托辞。
    """

    status: CreateReplacementStatus
    replacement: ReplacementRecord | None = None
    existing_replacement_key: str | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _validate_status_and_payload_are_exclusive(self) -> "CreateReplacementResult":
        if self.status is CreateReplacementStatus.CREATED:
            if self.replacement is None:
                raise ValueError("created 结果必须携带真实持久化的换货单")
            if self.failure_reason is not None:
                raise ValueError("created 结果不得携带失败原因")
        else:
            if self.replacement is not None:
                raise ValueError("失败结果不得携带换货单")
            if self.failure_reason is None:
                raise ValueError("失败结果必须携带结构化失败原因")

        if self.status is CreateReplacementStatus.DUPLICATE:
            if self.existing_replacement_key is None:
                raise ValueError("duplicate 结果必须指出已存在的换货单标识")
        elif self.existing_replacement_key is not None:
            raise ValueError("只有 duplicate 结果才可携带已存在的换货单标识")
        return self
