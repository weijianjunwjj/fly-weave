"""policy document ingestion 的显式类型契约与 deterministic chunking（T024）。

本模块定义 PolicyDocument / PolicyChunk 的应用层契约、canonical content 规范
化、content identity 计算，以及 deterministic 的 paragraph/section-aware
chunking。所有这些都不依赖 LLM：同样输入必然产生同样的 canonical content、
content identity 与 chunk 列表。

这是 T024 的 ingestion baseline。retrieval / ranking / RAG 不在此模块，也不
在此任务范围内。
"""
import hashlib
import re
from enum import Enum

from pydantic import BaseModel, Field, model_validator


class PolicyDocumentInput(BaseModel):
    """可摄取的政策文档输入。

    ``business_key`` 是稳定文档业务标识，``source_reference`` 是稳定来源定位符。
    二者都是唯一键，共同构成"同一份政策文档"的身份，供 ingestion 幂等判定。
    ``raw_content`` 是待摄取的政策原文，服务端会先规范化再计算 identity 与分块。
    """

    business_key: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_reference: str = Field(min_length=1)
    raw_content: str
    is_demo_data: bool = True

    @model_validator(mode="after")
    def _validate_content_not_blank(self) -> "PolicyDocumentInput":
        if not self.raw_content or not self.raw_content.strip():
            raise ValueError("policy document content 为空，无法 ingestion")
        return self


class PolicyDocumentIngestionStatus(str, Enum):
    """ingestion 结果状态。

    - ``CREATED``：首次摄取，新建 document 与 chunks；
    - ``UNCHANGED``：同一 source + 同一 content 的重复摄取，幂等，不产生新记录；
    - ``UPDATED``：content 变化，version 递增并原子替换 chunks。
    """

    CREATED = "created"
    UNCHANGED = "unchanged"
    UPDATED = "updated"


class PolicyDocumentIngestionResult(BaseModel):
    """ingestion 结果：明确区分首次创建、内容未变（幂等）与内容变化（版本替换）。"""

    status: PolicyDocumentIngestionStatus
    document_key: str
    version: int
    content_identity: str
    chunk_count: int
    chunk_keys: list[str] = Field(default_factory=list)


def canonicalize_content(raw_content: str) -> str:
    """把政策原文规范化为 canonical content。

    规范化规则是确定性的：
    - 统一换行为 ``\\n``；
    - 去除每行行尾空白；
    - 去除首尾的空行；
    - 把连续多个空行折叠为单个空行。

    规范化后的文本是 content identity 与 chunking 的权威输入。
    """
    text = raw_content.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()

    collapsed: list[str] = []
    for line in lines:
        if line == "" and collapsed and collapsed[-1] == "":
            continue
        collapsed.append(line)
    return "\n".join(collapsed)


def content_identity_of(canonical_content: str) -> str:
    """计算 canonical content 的稳定身份指纹（sha256 hex digest）。

    同一 content 必然得到同一 identity，是幂等摄取与变更检测的唯一依据。
    """
    return hashlib.sha256(canonical_content.encode("utf-8")).hexdigest()


def chunk_canonical_content(canonical_content: str) -> list[str]:
    """把 canonical content 切分为有序 chunk 列表。

    使用 deterministic 的 paragraph/section-aware 分块：按一个或多个空行分隔的
    段落（block）即一个 chunk。不调用 LLM，不做 token-aware pipeline，不构造
    hierarchical chunk graph。同样输入必然产生同样数量、同样文本、同样顺序的
    chunks。
    """
    if not canonical_content.strip():
        return []
    blocks = re.split(r"\n{2,}", canonical_content)
    return [block.strip() for block in blocks if block.strip()]


def chunk_policy_document(raw_content: str) -> list[str]:
    """对外入口：先规范化原文，再切分为有序 chunks。"""
    return chunk_canonical_content(canonicalize_content(raw_content))
