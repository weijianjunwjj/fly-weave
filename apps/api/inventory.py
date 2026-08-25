"""check_inventory Tool 的显式类型契约。

T014 让 Agent 通过一个明确的 Tool / application boundary 查询真实的换货库存
可用性。契约由应用拥有：输入、输出与失败形态都在这里定义，LLM 既不能定义
schema，也不能成为库存事实或可用性判定的来源。

本模块只定义契约，不包含任何查询逻辑。真实查询由 ``inventory_service`` 依据
持久化的 ``InventoryItem`` 记录完成。

可用性语义与 get_order 的关键区别：库存查询的"成功"并不保证有货。已持久化
但数量为零的 SKU 必须被表示为显式的 ``UNAVAILABLE`` 结果，而不是被编造成
有货，也不应与"查不到该 SKU"或"输入非法"混为一谈。
"""
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InventoryCheckStatus(str, Enum):
    """check_inventory 结果状态。

    - ``SUCCESS``：该 SKU 在持久化库存中存在，且可用数量大于零；
    - ``UNAVAILABLE``：该 SKU 在持久化库存中存在，但当前可用数量为零，
      显式表示"无货"，绝不伪造可用性；
    - ``SKU_NOT_FOUND``：持久化库存中不存在该 SKU；
    - ``INVALID_REQUEST``：请求不是已验证的 ``CheckInventoryRequest``。
    """

    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    SKU_NOT_FOUND = "sku_not_found"
    INVALID_REQUEST = "invalid_request"


class CheckInventoryRequest(BaseModel):
    """check_inventory 的输入契约：当前业务场景所需的最小库存标识。

    ``product_sku`` 对应 ``InventoryItem.product_sku`` —— 现有 domain 已支持的
    稳定、唯一的库存标识，而不是自增主键。与 get_order 一样，这里刻意不提供
    任何 filter / 排序 / 条件表达能力，输入只能是"一个明确的 SKU"，不存在
    可被滥用的通用查询面。

    校验在进入数据库之前完成：空标识、纯空白、非法字符、超长标识与缺字段都在
    schema 层直接失败。``extra="forbid"`` 使调用方无法夹带额外字段来试探出
    任意过滤能力。
    """

    model_config = ConfigDict(extra="forbid")

    # 与 InventoryItem.product_sku 的列宽（String(64)）保持一致，非法 SKU
    # 不会进入查询；正则同时覆盖了既有种子 SKU（SKU-EARBUD-PRO-01、
    # SKU-HEADSET-X-02）的命名约定
    product_sku: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )


class InventoryFacts(BaseModel):
    """成功 / 无货结果携带的库存事实，字段全部来自持久化的 ``InventoryItem``。

    ``available_quantity`` 直接反映数据库中的真实可用数量，包括零。零不是
    缺失：它表示"该商品存在但无货"，这是 ``UNAVAILABLE`` 结果的事实依据，
    与查无此 SKU 完全不同。
    """

    product_sku: str
    product_name: str
    available_quantity: int = Field(ge=0)
    warehouse: str
    is_demo_data: bool


class InventoryCheckResult(BaseModel):
    """库存查询结果：携带事实时字段来自真实库存，失败时只携带结构化原因。

    ``model_validator`` 强制各状态与字段互斥：

    - ``SUCCESS`` / ``UNAVAILABLE`` 必须携带真实库存事实，且不得携带失败原因；
    - ``SKU_NOT_FOUND`` / ``INVALID_REQUEST`` 不得携带库存事实，且必须携带
      结构化失败原因与 ``requested_sku``。

    因此即使在应用内部也无法构造出"没有库存记录却宣称成功"或"数量为零却
    宣称有货"的伪结果，空对象同样无法冒充成功。
    """

    status: InventoryCheckStatus
    inventory: InventoryFacts | None = None
    requested_sku: str | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _validate_status_and_payload_are_exclusive(self) -> "InventoryCheckResult":
        has_facts = self.status in (
            InventoryCheckStatus.SUCCESS,
            InventoryCheckStatus.UNAVAILABLE,
        )

        if has_facts:
            if self.inventory is None:
                raise ValueError(
                    "success / unavailable 结果必须携带真实库存事实"
                )
            if self.failure_reason is not None:
                raise ValueError("success / unavailable 结果不得携带失败原因")
        else:
            if self.inventory is not None:
                raise ValueError("失败结果不得携带库存事实")
            if self.failure_reason is None:
                raise ValueError("失败结果必须携带结构化失败原因")

        # INVALID_REQUEST 来自无法从原始文本中提取 SKU 的场景，允许 requested_sku
        # 为空；其余三种状态都必须明确指出被请求的 SKU
        if self.status is not InventoryCheckStatus.INVALID_REQUEST and self.requested_sku is None:
            raise ValueError("结果必须指出被请求的 SKU")
        return self
