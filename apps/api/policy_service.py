"""deterministic 的售后政策查询服务。

这是 Golden Path 的 policy grounding 基线：把已验证的 structured intent
通过 application-owned 映射精确解析为政策 business_key，再从持久化的
``AfterSalesPolicy`` 读取真实的 source identity / metadata / rule。

本模块不包含 embeddings、pgvector、向量检索、语义搜索、RAG 或 LLM reranker。
查询结果绝不来自模型文本：模型只能提供 ``ReplacementIntent``，政策内容与
成功判定完全由本服务依据数据库中的真实记录决定。
"""
from sqlalchemy.orm import Session

from intents import IntentType, RequestedAction, ReplacementIntent
from models import AfterSalesPolicy
from policies import (
    PolicyLookupResult,
    PolicyLookupStatus,
    PolicySource,
    ReplacementPolicyRule,
    SUPPORTED_POLICY_KEYS,
)


def lookup_replacement_policy(
    db: Session, intent: ReplacementIntent
) -> PolicyLookupResult:
    """根据已验证的 structured intent 查询换货政策。

    输入必须是 ``ReplacementIntent`` 类型；任何非结构化文本（例如模型的原始
    输出字符串）都在 boundary 处被拒绝，返回 ``UNSUPPORTED_QUERY``，绝不进入
    成功路径。只有映射表中明确登记的 intent 组合才可查到政策。
    """
    if not isinstance(intent, ReplacementIntent):
        return PolicyLookupResult(
            status=PolicyLookupStatus.UNSUPPORTED_QUERY,
            failure_reason=(
                "policy lookup 只接受已验证的 ReplacementIntent，"
                "不接受模型原始文本"
            ),
        )

    query_key = (intent.intent_type, intent.requested_action)
    if query_key not in SUPPORTED_POLICY_KEYS:
        return PolicyLookupResult(
            status=PolicyLookupStatus.UNSUPPORTED_QUERY,
            failure_reason=(
                f"不支持的政策查询意图: intent_type={intent.intent_type.value}, "
                f"requested_action={intent.requested_action.value}"
            ),
        )

    policy_key = SUPPORTED_POLICY_KEYS[query_key]
    policy = (
        db.query(AfterSalesPolicy)
        .filter(AfterSalesPolicy.business_key == policy_key)
        .one_or_none()
    )
    if policy is None:
        return PolicyLookupResult(
            status=PolicyLookupStatus.POLICY_NOT_FOUND,
            failure_reason=f"未找到售后政策: {policy_key}",
        )

    return PolicyLookupResult(
        status=PolicyLookupStatus.SUCCESS,
        source=PolicySource(
            policy_key=policy.business_key,
            title=policy.title,
            source_reference=policy.source_reference,
            is_demo_data=policy.is_demo_data,
        ),
        rule=ReplacementPolicyRule(
            replacement_window_days=policy.replacement_window_days,
            approval_required_above_amount=policy.approval_required_above_amount,
        ),
    )
