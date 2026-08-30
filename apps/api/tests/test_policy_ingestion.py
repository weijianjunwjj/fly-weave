"""T024 policy document ingestion baseline 的确定性测试。

覆盖任务要求的验收点：
- first ingestion 创建 document 与 chunks；
- 相同 source + 相同 content 重复摄取幂等，不产生重复记录；
- chunk identity 与 order 稳定；
- source metadata 被保留；
- document → chunks 可追溯；
- content 变化采用确定性版本/替换语义，不混合新旧 chunks；
- 跨新 DB Session 的 durability；
- demo reset / reseed 确定性；
- empty / invalid input 安全失败；
- ingestion 不依赖 LLM；
- 现有 policy lookup Golden Path 不回归。
"""
import pytest
from pydantic import ValidationError

from database import SessionLocal
from demo_policy_source import DEMO_REPLACEMENT_POLICY_DOCUMENT
from intents import IntentType, ReplacementIntent, RequestedAction
from models import PolicyChunk, PolicyDocument
from policies import PolicyLookupStatus
from policy_documents import (
    PolicyDocumentIngestionStatus,
    PolicyDocumentInput,
    canonicalize_content,
    chunk_policy_document,
    content_identity_of,
)
from policy_ingestion_service import ingest_policy_document
from policy_service import lookup_replacement_policy
from seed_data import clear_demo_data, seed_demo_data

DEMO_DOC_KEY = DEMO_REPLACEMENT_POLICY_DOCUMENT.business_key


def _replacement_intent() -> ReplacementIntent:
    """构造一条通过 validation boundary 的 quality issue / replacement intent。"""
    return ReplacementIntent.model_validate(
        {
            "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT,
            "issue_summary": "右耳耳机无声，疑似质量问题",
            "requested_action": RequestedAction.REPLACEMENT,
            "confidence": 0.95,
        }
    )


def _doc_input(**overrides) -> PolicyDocumentInput:
    """构造一条可摄取的政策文档输入，business_key / source_reference 可覆盖。"""
    payload = {
        "business_key": "policy-doc-test-ingestion",
        "title": "测试换货政策",
        "source_reference": "policy-doc://test/ingestion",
        "raw_content": "换货政策总则\n这是一份测试政策。\n\n换货时间窗口\n30 天内可换货。",
        "is_demo_data": True,
    }
    payload.update(overrides)
    return PolicyDocumentInput.model_validate(payload)


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据，保证查询目标始终存在且状态可预测。"""
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


def _chunks_for(db, document_key: str) -> list[PolicyChunk]:
    doc = (
        db.query(PolicyDocument)
        .filter(PolicyDocument.business_key == document_key)
        .one()
    )
    return (
        db.query(PolicyChunk)
        .filter(PolicyChunk.document_id == doc.id)
        .order_by(PolicyChunk.chunk_order.asc())
        .all()
    )


def _doc_count(db, document_key: str) -> int:
    return (
        db.query(PolicyDocument)
        .filter(PolicyDocument.business_key == document_key)
        .count()
    )


def test_first_ingestion_creates_document_and_chunks():
    """首次摄取创建 document 与 chunks，version=1，chunk 数量与结果一致"""
    db = SessionLocal()
    try:
        result = ingest_policy_document(db, _doc_input())

        assert result.status is PolicyDocumentIngestionStatus.CREATED
        assert result.version == 1
        assert result.chunk_count == 2
        assert len(result.chunk_keys) == result.chunk_count

        doc = (
            db.query(PolicyDocument)
            .filter(PolicyDocument.business_key == "policy-doc-test-ingestion")
            .one()
        )
        assert doc.version == 1
        assert doc.content_identity == result.content_identity

        chunks = _chunks_for(db, "policy-doc-test-ingestion")
        assert len(chunks) == result.chunk_count
    finally:
        db.close()


def test_repeated_same_ingestion_is_idempotent():
    """相同 source + 相同 content 重复摄取幂等，不产生重复 document/chunk"""
    db = SessionLocal()
    try:
        doc_input = _doc_input()
        first = ingest_policy_document(db, doc_input)
        second = ingest_policy_document(db, doc_input)

        assert first.status is PolicyDocumentIngestionStatus.CREATED
        assert second.status is PolicyDocumentIngestionStatus.UNCHANGED
        assert second.version == first.version
        assert second.content_identity == first.content_identity
        assert second.chunk_count == first.chunk_count
        assert second.chunk_keys == first.chunk_keys

        assert _doc_count(db, doc_input.business_key) == 1
        assert len(_chunks_for(db, doc_input.business_key)) == first.chunk_count
    finally:
        db.close()


def test_stable_chunk_identities_and_order():
    """chunk identity 与 order 稳定，重复摄取返回同一组 chunk keys"""
    db = SessionLocal()
    try:
        doc_input = _doc_input()
        ingest_policy_document(db, doc_input)

        chunks = _chunks_for(db, doc_input.business_key)
        expected_keys = [
            f"{doc_input.business_key}#chunk-0001",
            f"{doc_input.business_key}#chunk-0002",
        ]
        assert [c.business_key for c in chunks] == expected_keys
        assert [c.chunk_order for c in chunks] == [1, 2]
        # 稳定顺序：与 chunker 输出顺序一致
        assert [c.text for c in chunks] == chunk_policy_document(doc_input.raw_content)

        # 重复摄取后 keys 完全一致
        result = ingest_policy_document(db, doc_input)
        assert result.chunk_keys == expected_keys
    finally:
        db.close()


def test_source_metadata_preserved():
    """chunk 与 document 都保留 source_reference，来源身份不丢失"""
    db = SessionLocal()
    try:
        doc_input = _doc_input(source_reference="policy-doc://test/metadata")
        ingest_policy_document(db, doc_input)

        doc = (
            db.query(PolicyDocument)
            .filter(PolicyDocument.business_key == doc_input.business_key)
            .one()
        )
        assert doc.source_reference == "policy-doc://test/metadata"

        for chunk in _chunks_for(db, doc_input.business_key):
            assert chunk.source_reference == doc.source_reference
    finally:
        db.close()


def test_document_to_chunks_traceable():
    """每个 chunk 都能明确追溯到原始 PolicyDocument"""
    db = SessionLocal()
    try:
        doc_input = _doc_input()
        ingest_policy_document(db, doc_input)

        doc = (
            db.query(PolicyDocument)
            .filter(PolicyDocument.business_key == doc_input.business_key)
            .one()
        )
        for chunk in _chunks_for(db, doc_input.business_key):
            assert chunk.document_id == doc.id
            assert chunk.document.business_key == doc.business_key
    finally:
        db.close()


def test_changed_content_bumps_version_and_replaces_chunks():
    """content 变化时 version 递增并原子替换 chunks，不混合新旧内容"""
    db = SessionLocal()
    try:
        doc_input = _doc_input()
        first = ingest_policy_document(db, doc_input)
        old_chunk_texts = [c.text for c in _chunks_for(db, doc_input.business_key)]

        changed = _doc_input(
            raw_content="换货政策总则\n更新后的政策。\n\n审批依据\n超过 1000 元需审批。\n\n换货时间窗口\n15 天内可换货。"
        )
        second = ingest_policy_document(db, changed)

        assert second.status is PolicyDocumentIngestionStatus.UPDATED
        assert second.version == first.version + 1
        assert second.content_identity != first.content_identity
        assert second.chunk_count == 3

        # 同一 document 只有一份，version 已更新
        assert _doc_count(db, doc_input.business_key) == 1
        doc = (
            db.query(PolicyDocument)
            .filter(PolicyDocument.business_key == doc_input.business_key)
            .one()
        )
        assert doc.version == second.version
        assert doc.content_identity == second.content_identity

        new_chunks = _chunks_for(db, doc_input.business_key)
        new_chunk_texts = [c.text for c in new_chunks]
        # 新 chunks 与 chunker 输出一致，且不含任何旧 chunk 文本
        assert new_chunk_texts == chunk_policy_document(changed.raw_content)
        assert set(new_chunk_texts).isdisjoint(set(old_chunk_texts))
        assert [c.chunk_order for c in new_chunks] == [1, 2, 3]
    finally:
        db.close()


def test_durability_across_new_session():
    """摄取结果跨新 DB Session 依然可读"""
    db = SessionLocal()
    try:
        doc_input = _doc_input(business_key="policy-doc-test-durable",
                               source_reference="policy-doc://test/durable")
        ingest_policy_document(db, doc_input)
    finally:
        db.close()

    fresh = SessionLocal()
    try:
        assert _doc_count(fresh, "policy-doc-test-durable") == 1
        chunks = _chunks_for(fresh, "policy-doc-test-durable")
        assert len(chunks) > 0
    finally:
        fresh.close()


def test_empty_and_invalid_content_fail_safely():
    """空 / 全空白 content 在 boundary 处安全失败，绝不落库"""
    with pytest.raises(ValidationError):
        PolicyDocumentInput.model_validate(
            {
                "business_key": "policy-doc-test-empty",
                "title": "空政策",
                "source_reference": "policy-doc://test/empty",
                "raw_content": "",
            }
        )

    with pytest.raises(ValidationError):
        PolicyDocumentInput.model_validate(
            {
                "business_key": "policy-doc-test-blank",
                "title": "空白政策",
                "source_reference": "policy-doc://test/blank",
                "raw_content": "   \n\t\n  ",
            }
        )


def test_chunking_is_deterministic_and_requires_no_llm():
    """chunking 与 identity 是纯函数：同样输入得到同样输出，无需 LLM"""
    text = DEMO_REPLACEMENT_POLICY_DOCUMENT.raw_content

    assert chunk_policy_document(text) == chunk_policy_document(text)
    assert content_identity_of(canonicalize_content(text)) == (
        content_identity_of(canonicalize_content(text))
    )

    # CRLF / 行尾空白会被规范化，chunk 结果不受影响
    normalized = "标题\n正文。\r\n\r\n下一段。"
    assert chunk_policy_document(normalized) == ["标题\n正文。", "下一段。"]


def test_demo_seed_is_deterministic_for_knowledge():
    """重复 seed 后 knowledge document/chunk 状态确定"""
    db = SessionLocal()
    try:
        seed_demo_data(db)
        seed_demo_data(db)

        docs = db.query(PolicyDocument).filter(PolicyDocument.is_demo_data == True).all()
        assert len(docs) == 1
        assert docs[0].business_key == DEMO_DOC_KEY

        chunks = (
            db.query(PolicyChunk)
            .filter(PolicyChunk.document_id == docs[0].id)
            .order_by(PolicyChunk.chunk_order.asc())
            .all()
        )
        assert len(chunks) > 0
        assert [c.chunk_order for c in chunks] == list(range(1, len(chunks) + 1))
    finally:
        db.close()


def test_clear_demo_data_removes_knowledge_documents_and_chunks():
    """clear demo data 清理 knowledge document/chunk，不遗留阻塞下次 seed 的数据"""
    db = SessionLocal()
    try:
        seed_demo_data(db)
        assert db.query(PolicyDocument).filter(PolicyDocument.is_demo_data == True).count() == 1

        clear_demo_data(db)

        assert db.query(PolicyDocument).filter(PolicyDocument.is_demo_data == True).count() == 0
        assert db.query(PolicyChunk).filter(PolicyChunk.is_demo_data == True).count() == 0
    finally:
        db.close()


def test_policy_lookup_golden_path_still_works_after_ingestion():
    """ingestion 之后，现有 deterministic policy lookup Golden Path 不回归"""
    db = SessionLocal()
    try:
        result = lookup_replacement_policy(db, _replacement_intent())
        assert result.status is PolicyLookupStatus.SUCCESS
        assert result.source.policy_key == "policy-replacement-standard"
        assert result.rule.replacement_window_days == 30
    finally:
        db.close()


def test_policy_tables_exist_after_migration():
    """migration smoke：policy_documents / policy_chunks 表已存在"""
    from sqlalchemy import inspect

    db = SessionLocal()
    try:
        tables = set(inspect(db.bind).get_table_names())
        assert "policy_documents" in tables
        assert "policy_chunks" in tables
    finally:
        db.close()
