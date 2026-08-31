"""policy retrieval 的显式类型契约与 deterministic 检索纯函数（T025）。

本模块定义 Policy Retrieval boundary 的输入 / 输出契约，以及不依赖任何 LLM、
embedding 或云端服务的确定性 lexical scoring 纯函数。检索策略刻意选择最小的
真实检索：

    - 输入只接受 ``PolicyRetrievalQuery``（由已验证的 structured intent +
      product/service context 构造），绝不接受模型自由文本；
    - 目标知识文档由 application-owned 映射（``RETRIEVAL_CORPUS_KEYS``）确定性
      选择，而不是让模型挑选；
    - relevance 由 ``extract_query_terms`` + ``score_passage`` 的确定性 lexical
      overlap 计算，同样输入必然得到同样 score，核心测试无需任何 embedding。

这是 T025 的 retrieval baseline，不是通用 RAG framework / hybrid search /
embedding router / reranker。真正的检索执行（查询 PolicyDocument / PolicyChunk
并排序）在 ``policy_retrieval_service`` 中完成。
"""
import re
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from intents import IntentType, RequestedAction, ReplacementIntent

# 从 human-readable 文本中抽取的 ASCII 词 token（长度 >= 2，避免把单个字母/数字
# 或 SKU 中的连字符片段当成有意义的检索词）。
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]{2,}")
# 中日韩字符（当前领域文本为中文）。用于构造字符 bigram 作为检索词。
_CJK_CHAR_RE = re.compile(r"[一-鿿]")


class PolicyRetrievalStatus(str, Enum):
    """policy retrieval 结果状态，显式区分每一种失败。

    - ``SUCCESS``：检索到至少一条 relevant passage；
    - ``NO_RELEVANT_POLICY``：corpus 存在、query 有效，但没有 passage 达到
      relevance 门槛；
    - ``CORPUS_UNAVAILABLE``：目标知识文档或 chunks 不存在（corpus 不可用）；
    - ``MALFORMED_QUERY``：query 不是已验证的 typed 对象（模型自由文本 / 未经验证
      输入），或 typed query 没有可搜索的语义内容；
    - ``UNSUPPORTED_QUERY``：query 是 typed 对象，但其 intent/action 组合未在
      retrieval corpus 映射中登记；
    - ``RETRIEVAL_FAILURE``：检索基础设施（数据库查询）发生未预期故障。

    没有"失败却默认允许换货"的取值：任何非 SUCCESS 都必须进入安全 decision path。
    """

    SUCCESS = "success"
    NO_RELEVANT_POLICY = "no_relevant_policy"
    CORPUS_UNAVAILABLE = "corpus_unavailable"
    MALFORMED_QUERY = "malformed_query"
    UNSUPPORTED_QUERY = "unsupported_query"
    RETRIEVAL_FAILURE = "retrieval_failure"


class PolicyRetrievalQuery(BaseModel):
    """policy retrieval 的已验证输入。

    字段全部来自已验证的业务上下文：``intent_type`` / ``requested_action`` /
    ``issue_summary`` 来自 structured intent，``product_sku`` / ``product_name``
    来自关联的订单 / 库存事实。这里不存在任何由模型自由文本直接充当的业务字段。
    """

    intent_type: IntentType
    requested_action: RequestedAction
    issue_summary: str = Field(min_length=1)
    # 相关产品 / 服务上下文。SKU 是稳定标识，产品名是可读上下文，均参与可追溯性；
    # 只有 product_name 参与 lexical scoring（SKU 是标识符，不是语义文本）。
    product_sku: str | None = None
    product_name: str | None = None


class RetrievedPolicyPassage(BaseModel):
    """一条真实检索到的政策 passage，携带完整、可追溯的来源身份。

    ``rank`` 从 1 开始，``score`` 是 deterministic lexical overlap 的真实分数，
    二者都由 retrieval service 产生，绝不伪造。``passage`` 是 chunk 的原文文本，
    ``chunk_key`` / ``document_key`` / ``source_reference`` 是稳定来源身份，
    使 UI 与 decision layer 都能追溯到"哪份政策、哪段内容"。
    """

    rank: int
    score: float
    document_key: str
    document_title: str
    document_version: int
    chunk_key: str
    chunk_order: int
    source_reference: str
    passage: str
    is_demo_data: bool


class PolicyRetrievalResult(BaseModel):
    """policy retrieval 结果。

    ``model_validator`` 强制成功与失败互斥：SUCCESS 必须携带至少一条 passage，
    非 SUCCESS 一律不得携带 passage。这样"无来源却宣称检索成功"在应用内部也无法
    构造。
    """

    status: PolicyRetrievalStatus
    passages: list[RetrievedPolicyPassage] = Field(default_factory=list)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def _validate_success_requires_passages(self) -> "PolicyRetrievalResult":
        if self.status is PolicyRetrievalStatus.SUCCESS and not self.passages:
            raise ValueError("success 检索结果必须携带至少一条 retrieved passage")
        if self.status is not PolicyRetrievalStatus.SUCCESS and self.passages:
            raise ValueError("失败检索结果不得携带 retrieved passage")
        return self


# deterministic、application-owned 的 retrieval corpus 映射：由已验证的 structured
# intent 精确映射到 PolicyDocument.business_key。这是 baseline，不使用 embeddings /
# 向量检索 / 语义搜索 / reranker。新增其它 intent 时在此登记确定性映射，而不是
# 让模型或检索猜测该搜哪份文档。
RETRIEVAL_CORPUS_KEYS: dict[tuple[IntentType, RequestedAction], str] = {
    (IntentType.QUALITY_ISSUE_REPLACEMENT, RequestedAction.REPLACEMENT): (
        "policy-doc-replacement-standard"
    ),
}


def query_for_intent(
    intent: ReplacementIntent,
    *,
    product_sku: str | None = None,
    product_name: str | None = None,
) -> PolicyRetrievalQuery:
    """从已验证的 structured intent 与产品上下文构造 retrieval query。

    这是唯一进入 retrieval boundary 的入口：query 只由已验证 typed 上下文构造，
    不接受任何模型自由文本。
    """
    return PolicyRetrievalQuery(
        intent_type=intent.intent_type,
        requested_action=intent.requested_action,
        issue_summary=intent.issue_summary,
        product_sku=product_sku,
        product_name=product_name,
    )


def extract_query_terms(query: PolicyRetrievalQuery) -> frozenset[str]:
    """从已验证 query 中确定性抽取检索词。

    - ASCII：抽取长度 >= 2 的字母 / 数字 token（lowercase）；
    - CJK：抽取相邻字符 bigram。

    只使用 issue_summary 与 product_name（human-readable 语义文本），不使用
    product_sku（标识符）作为检索词。同样输入必然得到同一个 frozenset。
    """
    text = " ".join(part for part in (query.issue_summary, query.product_name) if part)
    terms: set[str] = set()

    lowered = text.lower()
    for token in _ASCII_TOKEN_RE.findall(lowered):
        terms.add(token)

    cjk_chars = _CJK_CHAR_RE.findall(lowered)
    for index in range(len(cjk_chars) - 1):
        terms.add(cjk_chars[index] + cjk_chars[index + 1])

    return frozenset(terms)


def score_passage(terms: frozenset[str], passage_text: str) -> float:
    """计算一条 passage 对给定检索词的确定性 lexical relevance 分数。

    分数 = 命中的检索词数量 / 检索词总数，落在 [0, 1]。命中判定是纯子串匹配：
    ASCII 词不区分大小写，CJK bigram 直接子串匹配。同样的 terms 与 passage 必然
    得到同样分数，这是真实 lexical overlap，不是 fake semantic result。
    """
    if not terms:
        return 0.0
    haystack = passage_text.lower()
    matched = sum(1 for term in terms if term in haystack)
    return matched / len(terms)
