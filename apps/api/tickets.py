"""update_ticket Tool 的显式类型契约。

T017 是 Golden Path 的最后一个写入 Tool：它把一次已经真实发生的执行结果回写
到售后工单。与 create_replacement 一样属于业务状态变更，因此契约同样严格：

- 输入必须携带 ``replacement_key``，即一张**已经持久化**的换货单的标识。工单的
  解决结果不允许由自由文本描述——"换货已完成"这句话不能解决工单，只有数据库里
  那一行换货单可以；
- 输入不含任何自述字段（没有 ``status``、没有 ``force``），工单的最终状态由
  ``ticket_service`` 依据校验结果写入，而不是由调用方指定；
- 输出的 ``UPDATED`` 必须携带一份 ``TicketRecord``，其每个字段都取自回写之后的
  真实工单行，因此"宣称回写成功却没有持久化"在契约层就无法构造；
- 失败一律不携带 ``TicketRecord``，也就无法被误读成"半成功"。

本模块只定义契约，不包含任何校验或写入逻辑。真实回写由 ``ticket_service`` 在
数据库事务中完成，Agent Run 的完成语义同样由它负责。
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import TicketResolution, TicketStatus


class UpdateTicketStatus(str, Enum):
    """update_ticket 结果状态。

    成功只有一种，失败按"为什么无法回写"显式分列，绝不折叠成一个笼统的 error：

    - ``UPDATED``：全部校验通过，工单状态与结果引用已真实写入数据库；
    - ``TICKET_NOT_FOUND``：请求的工单在持久化状态中不存在；
    - ``REPLACEMENT_NOT_FOUND``：请求引用的换货单在持久化状态中不存在，
      即拿一个查无实据的结果来结案；
    - ``REPLACEMENT_LINKAGE_INVALID``：换货单真实存在，但它不属于本工单或不是
      本次 Run 执行出来的，即拿别的案子的成果来结自己的案；
    - ``RUN_LINKAGE_INVALID``：Agent Run 未持久化，或与该工单不是同一业务上下文；
    - ``INVALID_REQUEST``：输入不是已验证的 typed 请求（例如模型原始文本）；
    - ``PERSISTENCE_FAILED``：校验通过但写入被持久化层拒绝，事务已回滚，
      工单保持回写前的状态。
    """

    UPDATED = "updated"
    TICKET_NOT_FOUND = "ticket_not_found"
    REPLACEMENT_NOT_FOUND = "replacement_not_found"
    REPLACEMENT_LINKAGE_INVALID = "replacement_linkage_invalid"
    RUN_LINKAGE_INVALID = "run_linkage_invalid"
    INVALID_REQUEST = "invalid_request"
    PERSISTENCE_FAILED = "persistence_failed"


class UpdateTicketRequest(BaseModel):
    """update_ticket 的输入契约。

    字段集合与 plan 中约定的 update_ticket 输入一致（工单、解决结果、结构化
    摘要），并沿用前序 Tool 已确立的标识约定：对外只用稳定的业务标识符，不暴露
    自增主键。

    ``resolution`` 取自 ``TicketResolution``，当前只有"换货单已创建"这一个真实
    取值，因此 ``replacement_key`` 是必填而非可选——本 Tool 不存在"没有任何真实
    成果却要结案"的合法输入。

    ``extra="forbid"`` 使调用方无法夹带 ``status`` 之类的字段来直接指定工单终态。
    """

    model_config = ConfigDict(extra="forbid")

    # 与 Ticket.business_key 的列宽与命名约定保持一致
    ticket_key: str = Field(
        min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    resolution: TicketResolution
    # 与 ReplacementOrder.business_key 的列宽与命名约定保持一致
    replacement_key: str = Field(
        min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"
    )
    # 回写到工单的结果摘要；纯空白在此直接失败，不允许用空白摘要冒充结案说明
    summary: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def _reject_blank_summary(self) -> "UpdateTicketRequest":
        if not self.summary.strip():
            raise ValueError("工单结果摘要不得为空白")
        return self


class TicketRecord(BaseModel):
    """回写之后工单持久化状态的类型化视图。

    每个字段都直接来自数据库中真实存在的 ``Ticket`` 行（``replacement_key`` 取自
    它所引用的那一行 ``ReplacementOrder``）。这里不存在任何由调用方或模型填写的
    字段，因此拿到一个 ``TicketRecord`` 就等价于"数据库里的工单确实是这个状态"。
    """

    ticket_key: str
    status: TicketStatus
    resolution: TicketResolution
    resolution_summary: str
    replacement_key: str
    resolved_at: datetime


class UpdateTicketResult(BaseModel):
    """update_ticket 结果：成功携带真实工单状态，失败只携带结构化原因。

    ``model_validator`` 强制两条互斥规则，使伪成功在应用内部也无法构造：

    1. ``UPDATED`` 必须携带 ``ticket``，且不得携带失败原因；
    2. 任何非 ``UPDATED`` 一律不得携带 ``ticket``，且必须给出失败原因——失败
       无法伪装成"工单已经处理了一半"。
    """

    status: UpdateTicketStatus
    ticket: TicketRecord | None = None
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _validate_status_and_payload_are_exclusive(self) -> "UpdateTicketResult":
        if self.status is UpdateTicketStatus.UPDATED:
            if self.ticket is None:
                raise ValueError("updated 结果必须携带真实持久化的工单状态")
            if self.failure_reason is not None:
                raise ValueError("updated 结果不得携带失败原因")
        else:
            if self.ticket is not None:
                raise ValueError("失败结果不得携带工单状态")
            if self.failure_reason is None:
                raise ValueError("失败结果必须携带结构化失败原因")
        return self
