"""T011 intent validation boundary 的纯函数测试。

这些测试只覆盖 extract_intent，不触碰数据库，因此标记为 no_database。
"""
import json

import pytest

from intents import (
    IntentExtractionStatus,
    IntentType,
    RequestedAction,
    extract_intent,
)


pytestmark = pytest.mark.no_database


def _valid_raw_output(**overrides) -> str:
    """构造一条合法的 quality issue / replacement 模型原始输出。"""
    payload = {
        "intent_type": "quality_issue_replacement",
        "issue_summary": "右耳耳机无声，疑似质量问题",
        "requested_action": "replacement",
        "confidence": 0.95,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_valid_structured_intent_is_accepted():
    """合法结构化 intent 通过校验，返回成功并携带全部字段"""
    outcome = extract_intent(_valid_raw_output())

    assert outcome.status is IntentExtractionStatus.SUCCESS
    assert outcome.intent is not None
    assert outcome.intent.intent_type is IntentType.QUALITY_ISSUE_REPLACEMENT
    assert outcome.intent.issue_summary == "右耳耳机无声，疑似质量问题"
    assert outcome.intent.requested_action is RequestedAction.REPLACEMENT
    assert outcome.intent.confidence == 0.95
    assert outcome.failure_reason is None
    assert outcome.validation_errors == []


def test_model_failure_when_output_is_absent_or_blank():
    """模型调用失败（无输出）进入 model_failure，而非成功"""
    for raw in (None, "", "   "):
        outcome = extract_intent(raw)
        assert outcome.status is IntentExtractionStatus.MODEL_FAILURE
        assert outcome.intent is None
        assert outcome.failure_reason


def test_invalid_model_output_not_json_is_rejected():
    """非法模型输出（不是 JSON）进入 invalid_output"""
    outcome = extract_intent("这不是 JSON")

    assert outcome.status is IntentExtractionStatus.INVALID_OUTPUT
    assert outcome.intent is None
    assert outcome.failure_reason


def test_invalid_model_output_not_object_is_rejected():
    """非法模型输出（JSON 但不是对象）进入 invalid_output"""
    outcome = extract_intent(json.dumps(["not", "an", "object"]))

    assert outcome.status is IntentExtractionStatus.INVALID_OUTPUT
    assert outcome.intent is None
    assert outcome.failure_reason


def test_unsupported_intent_is_rejected():
    """不受支持的 intent 类型进入 unsupported_intent，而非成功"""
    outcome = extract_intent(_valid_raw_output(intent_type="refund_request"))

    assert outcome.status is IntentExtractionStatus.UNSUPPORTED_INTENT
    assert outcome.intent is None
    assert "refund_request" in outcome.failure_reason


def test_validation_failure_on_missing_field():
    """缺字段进入 validation_failed，并携带结构化校验错误"""
    raw = json.dumps(
        {
            "intent_type": "quality_issue_replacement",
            "requested_action": "replacement",
            "confidence": 0.9,
        }
    )
    outcome = extract_intent(raw)

    assert outcome.status is IntentExtractionStatus.VALIDATION_FAILED
    assert outcome.intent is None
    assert outcome.failure_reason
    assert any("issue_summary" in error for error in outcome.validation_errors)


def test_validation_failure_on_out_of_range_confidence():
    """越界的 confidence 进入 validation_failed，而非成功"""
    outcome = extract_intent(_valid_raw_output(confidence=1.5))

    assert outcome.status is IntentExtractionStatus.VALIDATION_FAILED
    assert outcome.intent is None
    assert any("confidence" in error for error in outcome.validation_errors)


def test_validation_failure_on_wrong_requested_action():
    """不支持的客户诉求进入 validation_failed"""
    outcome = extract_intent(_valid_raw_output(requested_action="repair"))

    assert outcome.status is IntentExtractionStatus.VALIDATION_FAILED
    assert outcome.intent is None


def test_validation_failure_on_non_string_intent_type():
    """intent_type 类型错误由 schema 校验捕获为 validation_failed"""
    outcome = extract_intent(_valid_raw_output(intent_type=123))

    assert outcome.status is IntentExtractionStatus.VALIDATION_FAILED
    assert outcome.intent is None
