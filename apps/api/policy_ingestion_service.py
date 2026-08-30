"""deterministic 的 policy document ingestion application service（T024）。

本服务把一份可摄取的政策文档：规范化 canonical content → 计算稳定 content
identity → deterministic chunking → 以原子方式持久化为 ``PolicyDocument`` 与
一组 ``PolicyChunk``。

幂等与版本语义（最小、清晰、可测试）：

- 同一 ``business_key`` 且同一 content（content_identity 相同）的重复摄取返回
  ``UNCHANGED``，不新增任何 document / chunk，也不递增 version；
- content 变化时 ``version += 1``，先删除旧 chunks 再写入新 chunks，绝不把旧
  chunks 与新 content 混在一起；
- 全部变更在同一事务内提交，要么成功要么整体回滚。

本服务不包含 embeddings、pgvector、retrieval、ranking、RAG 或任何 LLM 调用。
chunking 与 identity 均来自 ``policy_documents`` 的纯函数。
"""
from datetime import datetime

from sqlalchemy.orm import Session

from models import PolicyChunk, PolicyDocument
from policy_documents import (
    PolicyDocumentIngestionResult,
    PolicyDocumentIngestionStatus,
    PolicyDocumentInput,
    canonicalize_content,
    chunk_canonical_content,
    content_identity_of,
)

# chunk business_key 的固定后缀格式，保证同一文档同一序号在不同版本间标识稳定
_CHUNK_KEY_FORMAT = "{document_key}#chunk-{order:04d}"


def _build_chunks(
    document: PolicyDocument, chunks: list[str], source: PolicyDocumentInput
) -> list[PolicyChunk]:
    """按规范 chunk 顺序构造 PolicyChunk 对象。调用前 document 必须已 flush（有 id）。"""
    return [
        PolicyChunk(
            business_key=_CHUNK_KEY_FORMAT.format(
                document_key=document.business_key, order=order
            ),
            document_id=document.id,
            chunk_order=order,
            text=text,
            source_reference=source.source_reference,
            is_demo_data=source.is_demo_data,
        )
        for order, text in enumerate(chunks, start=1)
    ]


def ingest_policy_document(
    db: Session, source: PolicyDocumentInput
) -> PolicyDocumentIngestionResult:
    """摄取一份政策文档，返回幂等的 ingestion 结果。

    ``source`` 必须是已验证的 ``PolicyDocumentInput``；空 content 在 boundary
    已被拒绝，这里仍做防御性校验，绝不让空白内容落库。
    """
    canonical = canonicalize_content(source.raw_content)
    if not canonical.strip():
        raise ValueError("policy document content 为空，无法 ingestion")

    identity = content_identity_of(canonical)
    chunks = chunk_canonical_content(canonical)
    if not chunks:
        raise ValueError("policy document 切分后没有有效 chunk")

    existing = (
        db.query(PolicyDocument)
        .filter(PolicyDocument.business_key == source.business_key)
        .one_or_none()
    )

    if existing is not None:
        if existing.source_reference != source.source_reference:
            raise ValueError(
                f"business_key {source.business_key!r} 已绑定 source_reference "
                f"{existing.source_reference!r}，与输入 {source.source_reference!r} 冲突"
            )

        if existing.content_identity == identity:
            # 同一 source + 同一 content：幂等，不产生任何新记录
            ordered_chunks = (
                db.query(PolicyChunk)
                .filter(PolicyChunk.document_id == existing.id)
                .order_by(PolicyChunk.chunk_order.asc())
                .all()
            )
            return PolicyDocumentIngestionResult(
                status=PolicyDocumentIngestionStatus.UNCHANGED,
                document_key=existing.business_key,
                version=existing.version,
                content_identity=identity,
                chunk_count=len(ordered_chunks),
                chunk_keys=[chunk.business_key for chunk in ordered_chunks],
            )

        # content 变化：最小版本语义 = version 递增 + 原子替换 chunks
        existing.version += 1
        existing.title = source.title
        existing.raw_content = canonical
        existing.content_identity = identity
        existing.is_demo_data = source.is_demo_data
        existing.ingested_at = datetime.utcnow()

        db.query(PolicyChunk).filter(
            PolicyChunk.document_id == existing.id
        ).delete()
        db.flush()

        new_chunks = _build_chunks(existing, chunks, source)
        db.add_all(new_chunks)
        db.commit()

        return PolicyDocumentIngestionResult(
            status=PolicyDocumentIngestionStatus.UPDATED,
            document_key=existing.business_key,
            version=existing.version,
            content_identity=identity,
            chunk_count=len(new_chunks),
            chunk_keys=[chunk.business_key for chunk in new_chunks],
        )

    # 首次摄取前，防御 source_reference 被其它 business_key 占用
    source_conflict = (
        db.query(PolicyDocument)
        .filter(PolicyDocument.source_reference == source.source_reference)
        .one_or_none()
    )
    if source_conflict is not None:
        raise ValueError(
            f"source_reference {source.source_reference!r} 已被 "
            f"business_key {source_conflict.business_key!r} 占用"
        )

    document = PolicyDocument(
        business_key=source.business_key,
        title=source.title,
        source_reference=source.source_reference,
        version=1,
        content_identity=identity,
        raw_content=canonical,
        is_demo_data=source.is_demo_data,
        ingested_at=datetime.utcnow(),
    )
    db.add(document)
    db.flush()

    new_chunks = _build_chunks(document, chunks, source)
    db.add_all(new_chunks)
    db.commit()

    return PolicyDocumentIngestionResult(
        status=PolicyDocumentIngestionStatus.CREATED,
        document_key=document.business_key,
        version=document.version,
        content_identity=identity,
        chunk_count=len(new_chunks),
        chunk_keys=[chunk.business_key for chunk in new_chunks],
    )
