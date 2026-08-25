"""T012 baseline policy lookup 的确定性测试。

覆盖任务要求的验收点：
- seeded quality issue / replacement intent 能找到正确政策；
- source identity 被保留；
- metadata 可查询；
- unsupported / unknown 查询明确失败；
- 重复执行结果稳定；
- 不存在 model-generated fake policy success 路径。
"""
import pytest
from pydantic import ValidationError

from database import SessionLocal
from intents import IntentType, ReplacementIntent, RequestedAction
from models import AfterSalesPolicy
from policies import (
    PolicyLookupResult,
    PolicyLookupStatus,
    SUPPORTED_POLICY_KEYS,
)
from policy_service import lookup_replacement_policy
from seed_data import seed_demo_data

# 查询结果中的稳定来源定位符，与 seed_data 中播种的 policy 保持一致
EXPECTED_SOURCE_REFERENCE = "policy-doc://after-sales/v1#replacement"


def _replacement_intent(**overrides) -> ReplacementIntent:
    """构造一条通过 validation boundary 的 quality issue / replacement intent。"""
    payload = {
        "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT,
        "issue_summary": "右耳耳机无声，疑似质量问题",
        "requested_action": RequestedAction.REPLACEMENT,
        "confidence": 0.95,
    }
    payload.update(overrides)
    return ReplacementIntent.model_validate(payload)


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据，保证查询目标始终存在且状态可预测。

    测试内删除 / 变更 policy 行，随后由 fixture 在下一次播种时重建，不会
    污染其它测试。
    """
    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()

    yield

    db = SessionLocal()
    try:
        seed_demo_data(db)
    finally:
        db.close()


def _seeded_policy(db) -> AfterSalesPolicy:
    return (
        db.query(AfterSalesPolicy)
        .filter(AfterSalesPolicy.business_key == "policy-replacement-standard")
        .one()
    )


def test_seeded_replacement_intent_finds_correct_policy():
    """seeded replacement intent 能查到正确政策，且结果字段全部来自数据库真实记录"""
    db = SessionLocal()
    try:
        result = lookup_replacement_policy(db, _replacement_intent())

        assert result.status is PolicyLookupStatus.SUCCESS

        policy = _seeded_policy(db)
        assert result.source.policy_key == policy.business_key
        assert result.source.title == policy.title
        assert result.source.source_reference == policy.source_reference
        assert result.source.is_demo_data is policy.is_demo_data

        assert result.rule.replacement_window_days == policy.replacement_window_days
        assert (
            result.rule.approval_required_above_amount
            == policy.approval_required_above_amount
        )
    finally:
        db.close()


def test_source_identity_and_metadata_are_preserved():
    """source identity 与 metadata 随查询结果保留，可被 decision layer 引用"""
    db = SessionLocal()
    try:
        result = lookup_replacement_policy(db, _replacement_intent())

        assert result.status is PolicyLookupStatus.SUCCESS
        assert result.source.policy_key == "policy-replacement-standard"
        assert result.source.source_reference == EXPECTED_SOURCE_REFERENCE
        # 来源定位符在映射表中确定性登记，而不是模型或查询现场临时生成
        assert SUPPORTED_POLICY_KEYS[
            (IntentType.QUALITY_ISSUE_REPLACEMENT, RequestedAction.REPLACEMENT)
        ] == result.source.policy_key
        # 演示数据必须显式标记，避免与生产政策混淆
        assert result.source.is_demo_data is True
        assert result.rule.replacement_window_days == 30
    finally:
        db.close()


def test_repeated_lookup_is_stable():
    """重复执行返回同一政策依据；跨 session 与重新播种后结果保持一致"""
    db = SessionLocal()
    try:
        first = lookup_replacement_policy(db, _replacement_intent())
        second = lookup_replacement_policy(db, _replacement_intent())
        assert first == second

        # 重新播种（fixture 保证在测试前后各执行一次），再查一次仍一致
        seed_demo_data(db)
        third = lookup_replacement_policy(db, _replacement_intent())
        assert third == first
    finally:
        db.close()


def test_model_text_is_rejected_not_treated_as_success():
    """模型原始文本不得被当作政策查询输入，更不得包装成 success"""
    db = SessionLocal()
    try:
        result = lookup_replacement_policy(db, "模型说政策允许换货")

        assert result.status is PolicyLookupStatus.UNSUPPORTED_QUERY
        assert result.source is None
        assert result.rule is None
        assert result.failure_reason is not None
    finally:
        db.close()


def test_unmapped_intent_returns_unsupported_failure(monkeypatch):
    """未在映射表中登记的 intent 组合明确失败，不回退到任何猜测"""
    import policy_service

    # 替换服务层可见的映射表，模拟"该组合未登记"的情况
    monkeypatch.setattr(policy_service, "SUPPORTED_POLICY_KEYS", {})

    db = SessionLocal()
    try:
        result = lookup_replacement_policy(db, _replacement_intent())

        assert result.status is PolicyLookupStatus.UNSUPPORTED_QUERY
        assert result.source is None
        assert result.rule is None
        assert result.failure_reason is not None
    finally:
        db.close()


def test_known_policy_key_with_missing_row_returns_not_found():
    """映射表命中了 key，但数据库无对应政策行时，返回明确 POLICY_NOT_FOUND"""
    db = SessionLocal()
    try:
        policy = _seeded_policy(db)
        db.delete(policy)
        db.commit()

        result = lookup_replacement_policy(db, _replacement_intent())

        assert result.status is PolicyLookupStatus.POLICY_NOT_FOUND
        assert result.source is None
        assert result.rule is None
        assert result.failure_reason == "未找到售后政策: policy-replacement-standard"
    finally:
        db.close()


def test_success_result_cannot_be_constructed_without_source():
    """即使在应用内部也无法构造无来源、无规则却宣称 success 的伪成功结果"""
    with pytest.raises(ValidationError):
        PolicyLookupResult(status=PolicyLookupStatus.SUCCESS)
