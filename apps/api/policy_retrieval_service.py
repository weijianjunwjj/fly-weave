"""deterministic policy retrieval application service（T025）。

把已验证的 ``PolicyRetrievalQuery`` 解析为对已持久化 ``PolicyDocument`` /
``PolicyChunk`` 的确定性 lexical retrieval，返回带真实来源身份的
``PolicyRetrievalResult``：

    PolicyRetrievalQuery（已验证，来自 structured intent + product context）
        → retrieve_policy_passages
        → PolicyRetrievalResult（status + ranked passages + source identity）

本服务不包含 embeddings、pgvector、reranker、query rewriting、网页搜索或任何
LLM 调用。目标文档由 ``policy_retrieval`` 里的 application-owned 映射确定性
选择，relevance 由 ``policy_retrieval`` 的纯函数确定性计算。同样输入必然得到
同样结果，因此核心测试无需任何云端服务即可运行。

失败全部显式表示，绝不 fallback 成"肯定允许换货"：检索失败 / no-match 由调用方
（Golden Path 编排）进入安全 decision path。
"""
from sqlalchemy.orm import Session

from models import PolicyChunk, PolicyDocument
from policy_retrieval import (
    RETRIEVAL_CORPUS_KEYS,
    PolicyRetrievalQuery,
    PolicyRetrievalResult,
    PolicyRetrievalStatus,
    RetrievedPolicyPassage,
    extract_query_terms,
    score_passage,
)


def retrieve_policy_passages(
    db: Session, query: PolicyRetrievalQuery
) -> PolicyRetrievalResult:
    """对已持久化 policy corpus 执行确定性 lexical retrieval。

    输入必须是 ``PolicyRetrievalQuery``；任何非结构化输入（例如模型原始文本）都在
    boundary 处被拒绝，返回 ``MALFORMED_QUERY``，绝不进入查询与成功路径。成功与否
    完全由数据库里的真实 PolicyDocument / PolicyChunk 与确定性 relevance 决定。
    """
    if not isinstance(query, PolicyRetrievalQuery):
        return PolicyRetrievalResult(
            status=PolicyRetrievalStatus.MALFORMED_QUERY,
            failure_reason=(
                "policy retrieval 只接受已验证的 PolicyRetrievalQuery，"
                "不接受模型原始文本"
            ),
        )

    document_key = RETRIEVAL_CORPUS_KEYS.get(
        (query.intent_type, query.requested_action)
    )
    if document_key is None:
        return PolicyRetrievalResult(
            status=PolicyRetrievalStatus.UNSUPPORTED_QUERY,
            failure_reason=(
                f"不支持的政策检索意图: intent_type={query.intent_type.value}, "
                f"requested_action={query.requested_action.value}"
            ),
        )

    terms = extract_query_terms(query)
    if not terms:
        return PolicyRetrievalResult(
            status=PolicyRetrievalStatus.MALFORMED_QUERY,
            failure_reason="检索查询没有可搜索的语义内容",
        )

    # 目标文档与 chunks 都来自数据库真实记录；查询过程中发生未预期故障时显式
    # 表示为 infrastructure failure，绝不据此构造成功结果。
    try:
        document = (
            db.query(PolicyDocument)
            .filter(PolicyDocument.business_key == document_key)
            .one_or_none()
        )
        if document is None:
            return PolicyRetrievalResult(
                status=PolicyRetrievalStatus.CORPUS_UNAVAILABLE,
                failure_reason=f"未找到政策知识文档: {document_key}",
            )

        chunks = (
            db.query(PolicyChunk)
            .filter(PolicyChunk.document_id == document.id)
            .order_by(PolicyChunk.chunk_order.asc())
            .all()
        )
    except Exception as exc:  # noqa: BLE001 - 基础设施故障必须显式传播而非吞掉
        return PolicyRetrievalResult(
            status=PolicyRetrievalStatus.RETRIEVAL_FAILURE,
            failure_reason=f"检索基础设施故障: {type(exc).__name__}",
        )

    if not chunks:
        return PolicyRetrievalResult(
            status=PolicyRetrievalStatus.CORPUS_UNAVAILABLE,
            failure_reason=f"政策知识文档 {document_key} 没有可用 chunks",
        )

    # 确定性排序：先按 relevance 分数降序，同分按 chunk_order 升序稳定 tie-break。
    scored = [(score_passage(terms, chunk.text), chunk) for chunk in chunks]
    relevant = [(score, chunk) for score, chunk in scored if score > 0.0]
    if not relevant:
        return PolicyRetrievalResult(
            status=PolicyRetrievalStatus.NO_RELEVANT_POLICY,
            failure_reason=(
                f"未检索到与查询相关的政策 passage: 文档 {document_key} 中无 chunk 命中"
            ),
        )

    relevant.sort(key=lambda pair: (-pair[0], pair[1].chunk_order))

    passages = [
        RetrievedPolicyPassage(
            rank=rank,
            score=score,
            document_key=document.business_key,
            document_title=document.title,
            document_version=document.version,
            chunk_key=chunk.business_key,
            chunk_order=chunk.chunk_order,
            source_reference=chunk.source_reference,
            passage=chunk.text,
            is_demo_data=chunk.is_demo_data,
        )
        for rank, (score, chunk) in enumerate(relevant, start=1)
    ]
    return PolicyRetrievalResult(
        status=PolicyRetrievalStatus.SUCCESS, passages=passages
    )
