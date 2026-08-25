"""structured intent 契约与 validation boundary。

T011 只支持一个业务意图：quality issue / replacement。

本模块定义显式 Pydantic schema 作为 intent 的契约，并提供一个纯函数
``extract_intent``，把模型的原始文本输出转换为"已验证的结构化 intent"或
"结构化的失败结果"。

LLM 不是业务执行器：模型输出在此处只被解析与校验，任何非法 / 缺字段 /
无法解析 / 不受支持的输出都不会进入成功路径，也不会被当作业务事实。
"""
import json
from enum import Enum

from pydantic import BaseModel, Field, ValidationError


class IntentType(str, Enum):
    """受支持的业务意图类型。当前仅 quality issue / replacement。"""

    QUALITY_ISSUE_REPLACEMENT = "quality_issue_replacement"


class RequestedAction(str, Enum):
    """客户诉求。当前仅支持换货。"""

    REPLACEMENT = "replacement"


class ReplacementIntent(BaseModel):
    """quality issue / replacement 的结构化意图契约。

    这是模型输出与应用状态之间的显式契约，不使用自由 JSON。字段集合由现有
    ticket / domain model 决定，保持最小且贴近业务：
    - ``intent_type``：明确的意图分类；
    - ``issue_summary``：质量问题摘要（后续 create_replacement 的 reason 依据）；
    - ``requested_action``：客户诉求；
    - ``confidence``：模型置信度，作为有界（0~1）的可验证状态。
    """

    intent_type: IntentType
    issue_summary: str = Field(min_length=1)
    requested_action: RequestedAction
    confidence: float = Field(ge=0.0, le=1.0)


class IntentExtractionStatus(str, Enum):
    """intent 抽取结果状态。"""

    SUCCESS = "success"
    MODEL_FAILURE = "model_failure"
    INVALID_OUTPUT = "invalid_output"
    UNSUPPORTED_INTENT = "unsupported_intent"
    VALIDATION_FAILED = "validation_failed"


class IntentExtractionOutcome(BaseModel):
    """抽取结果：成功时携带已验证 intent，失败时携带结构化失败原因。"""

    status: IntentExtractionStatus
    intent: ReplacementIntent | None = None
    failure_reason: str | None = None
    validation_errors: list[str] = Field(default_factory=list)


def _format_validation_errors(exc: ValidationError) -> list[str]:
    """把 pydantic 校验错误压平成可持久化的结构化字符串列表。"""
    errors: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        errors.append(f"{location}: {error.get('msg', 'unknown error')}")
    return errors


def extract_intent(raw_model_output: str | None) -> IntentExtractionOutcome:
    """模型输出与应用状态之间的 validation boundary。

    只有通过 JSON 解析、intent 类型支持检查与 Pydantic schema 校验，且
    intent type 为受支持的 quality_issue_replacement 时，才返回成功。其余
    情况一律返回结构化的失败结果，绝不构造伪成功 intent。
    """
    if raw_model_output is None or raw_model_output.strip() == "":
        return IntentExtractionOutcome(
            status=IntentExtractionStatus.MODEL_FAILURE,
            failure_reason="模型未返回任何输出",
        )

    try:
        data = json.loads(raw_model_output)
    except (json.JSONDecodeError, TypeError, ValueError):
        return IntentExtractionOutcome(
            status=IntentExtractionStatus.INVALID_OUTPUT,
            failure_reason="模型输出不是合法 JSON，无法解析",
        )

    if not isinstance(data, dict):
        return IntentExtractionOutcome(
            status=IntentExtractionStatus.INVALID_OUTPUT,
            failure_reason="模型输出不是 JSON 对象",
        )

    intent_type = data.get("intent_type")
    if (
        isinstance(intent_type, str)
        and intent_type != IntentType.QUALITY_ISSUE_REPLACEMENT.value
    ):
        return IntentExtractionOutcome(
            status=IntentExtractionStatus.UNSUPPORTED_INTENT,
            failure_reason=f"不支持的业务意图: {intent_type}",
        )

    try:
        intent = ReplacementIntent.model_validate(data)
    except ValidationError as exc:
        return IntentExtractionOutcome(
            status=IntentExtractionStatus.VALIDATION_FAILED,
            failure_reason="intent 校验失败",
            validation_errors=_format_validation_errors(exc),
        )

    return IntentExtractionOutcome(
        status=IntentExtractionStatus.SUCCESS,
        intent=intent,
    )
