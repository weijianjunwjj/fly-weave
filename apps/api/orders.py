"""get_order Tool 的显式类型契约。

T013 让 Agent 通过一个明确的 Tool / application boundary 查询真实订单事实。
契约由应用拥有：输入、输出与失败形态都在这里定义，LLM 既不能定义 schema，
也不能成为订单事实的来源。

本模块只定义契约，不包含任何查询逻辑。真实查询由 ``order_service`` 依据
持久化的 ``Order`` 记录完成。
"""
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import OrderStatus


class GetOrderRequest(BaseModel):
    """get_order 的输入契约：当前业务场景所需的最小订单标识。

    ``order_key`` 就是 ``Order.business_key`` —— 现有 domain 已支持的稳定订单
    标识符，而不是自增主键。这里刻意不提供任何 filter / 排序 / 条件表达能力：
    输入只能是"一个明确的订单标识"，因此不存在可被滥用的通用查询面。

    校验在进入数据库之前完成：空标识、纯空白、非法字符、超长标识与缺字段都在
    schema 层直接失败。``extra="forbid"`` 使调用方无法夹带额外字段来试探出
    任意过滤能力。
    """

    model_config = ConfigDict(extra="forbid")

    # 与 Order.business_key 的列宽（String(64)）和既有命名约定（order-demo-001）
    # 保持一致，非法 identifier 不会进入查询
    order_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class OrderFacts(BaseModel):
    """成功结果携带的订单事实，字段全部来自持久化的 ``Order`` 记录。

    字段集合以 Golden Path 后续判断的实际需要为准，不制造数据库中不存在的事实：
    - ``order_key`` / ``purchased_at`` / ``product_sku`` / ``status`` /
      ``customer_key``：plan 中 get_order 契约要求的订单事实；
    - ``product_name``：与 SKU 同源的商品名称，供 UI 与工单说明直接引用；
    - ``amount``：政策规则 ``approval_required_above_amount`` 需要与订单金额
      比较，缺少它则该确定性规则无法落地；
    - ``is_demo_data``：与 policy source 一致地显式标记演示数据，避免与生产
      订单混淆。

    ``status`` 复用 domain 的 ``OrderStatus``，使 Tool 输出的状态词表严格等于
    持久化词表，无法出现被编造的订单状态。
    """

    order_key: str
    customer_key: str
    product_sku: str
    product_name: str
    purchased_at: datetime
    status: OrderStatus
    amount: Decimal
    is_demo_data: bool


class OrderLookupStatus(str, Enum):
    """get_order 结果状态。"""

    SUCCESS = "success"
    ORDER_NOT_FOUND = "order_not_found"
    INVALID_REQUEST = "invalid_request"


class OrderLookupResult(BaseModel):
    """查询结果：成功时携带真实订单事实，失败时只携带结构化失败原因。

    ``model_validator`` 强制成功与失败路径互斥：SUCCESS 必须携带 order 且不得
    携带失败原因；任何非 SUCCESS 一律不得携带 order，且必须给出失败原因。因此
    即使在应用内部也无法构造出"没有订单却宣称成功"的伪成功结果，空对象同样
    无法冒充成功。

    ``requested_order_key`` 让失败结果能明确指出"被请求的是哪个标识"，
    ``failure_reason`` 只描述业务层事实（查不到该订单），不包含 SQL、内部异常
    或任何实现细节。
    """

    status: OrderLookupStatus
    order: OrderFacts | None = None
    requested_order_key: str | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _validate_success_and_failure_are_exclusive(self) -> "OrderLookupResult":
        if self.status is OrderLookupStatus.SUCCESS:
            if self.order is None:
                raise ValueError("success 结果必须携带真实订单事实")
            if self.failure_reason is not None:
                raise ValueError("success 结果不得携带失败原因")
        else:
            if self.order is not None:
                raise ValueError("失败结果不得携带订单事实")
            if self.failure_reason is None:
                raise ValueError("失败结果必须携带结构化失败原因")

        if (
            self.status is OrderLookupStatus.ORDER_NOT_FOUND
            and self.requested_order_key is None
        ):
            raise ValueError("order_not_found 必须指出被请求的订单标识")
        return self
