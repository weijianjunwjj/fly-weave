"""T025 policy retrieval / RAG grounding 的确定性测试。

覆盖任务要求的验收点：

- A. seeded 耳机质量问题 + 换货请求检索到真实 replacement policy passage；
- B. 不相关查询得到 no_relevant_policy，不伪造 replacement relevance；
- C. missing corpus 显式 retrieval failure；
- D. multiple chunks 排序稳定且重复调用结果可重复；
- E. PolicyDocument / PolicyChunk / title / source reference / passage / demo 元数据
  端到端一致，来源不由模型编造；
- malformed query（模型自由文本）与 unsupported intent 显式失败；
- retrieved source 到达 decision layer，deterministic facts 仍来自 structured lookup；
- retrieval 是真实可检查的 Agent Run step，并接入 T023 Audit Trail；
- retrieval failure fail closed，不变成默认允许换货，不绕过 Risk Gate；
- low-risk / approval-required / resume Golden Path 回归，policy basis 端到端暴露。

全部前置数据来自 ``seed_demo_data`` 的种子工单与知识文档，流程全程走真实实现，
测试不手工插入检索记录，也不伪造任何 source identity。
"""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent_run_service import run_golden_path
from database import SessionLocal
from decision_service import decide_replacement
from decisions import ReplacementDecisionStatus
from demo_policy_source import DEMO_REPLACEMENT_POLICY_DOCUMENT
from intents import (
    IntentExtractionOutcome,
    IntentExtractionStatus,
    IntentType,
    ReplacementIntent,
    RequestedAction,
)
from inventory import CheckInventoryRequest
from inventory_service import check_inventory
from main import app
from models import (
    AgentPolicyRetrieval,
    AgentRun,
    AgentRunStatus,
    AuditEvent,
    AuditEventType,
    PolicyChunk,
    PolicyDocument,
    ReplacementOrder,
    Ticket,
)
from order_service import get_order
from orders import GetOrderRequest
from policy_retrieval import (
    PolicyRetrievalResult,
    PolicyRetrievalStatus,
    query_for_intent,
)
from policy_retrieval_service import retrieve_policy_passages
from policy_service import lookup_replacement_policy
from seed_data import seed_demo_data

client = TestClient(app)

DEMO_DOC_KEY = DEMO_REPLACEMENT_POLICY_DOCUMENT.business_key
DEMO_DOC_TITLE = DEMO_REPLACEMENT_POLICY_DOCUMENT.title
DEMO_SOURCE_REF = DEMO_REPLACEMENT_POLICY_DOCUMENT.source_reference

LOW_RISK_TICKET_KEY = "ticket-demo-001"
LOW_RISK_ORDER_KEY = "order-demo-001"
HIGH_VALUE_TICKET_KEY = "ticket-demo-002"
AVAILABLE_SKU = "SKU-EARBUD-PRO-01"


def _replacement_intent(issue_summary: str = "右耳耳机无声，疑似质量问题，要求换货") -> ReplacementIntent:
    """构造一条通过 validation boundary 的 quality issue / replacement intent。"""
    return ReplacementIntent.model_validate(
        {
            "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT,
            "issue_summary": issue_summary,
            "requested_action": RequestedAction.REPLACEMENT,
            "confidence": 0.95,
        }
    )


@pytest.fixture(autouse=True)
def deterministic_state():
    """每个测试前后重新播种 demo 数据，保证 knowledge corpus 与种子工单可预测。"""
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


def _run(ticket_key: str) -> AgentRun:
    db = SessionLocal()
    try:
        ticket = db.query(Ticket).filter(Ticket.business_key == ticket_key).one()
        return run_golden_path(db, ticket)
    finally:
        db.close()


# --------------------------------------------------------------------------
# A / B / C / D：retrieval relevance 与失败
# --------------------------------------------------------------------------


def test_seeded_earbud_quality_issue_retrieves_relevant_replacement_passage():
    """耳机质量问题 + 换货请求检索到真实 replacement policy passage。"""
    db = SessionLocal()
    try:
        result = retrieve_policy_passages(
            db, query_for_intent(_replacement_intent())
        )

        assert result.status is PolicyRetrievalStatus.SUCCESS
        assert len(result.passages) >= 1

        for passage in result.passages:
            assert passage.document_key == DEMO_DOC_KEY
            assert passage.source_reference == DEMO_SOURCE_REF
            assert passage.is_demo_data is True
            assert passage.score > 0.0

        # 排名第一的 passage 与质量问题相关，且是真实 chunk 原文
        top = result.passages[0]
        assert top.rank == 1
        assert "质量" in top.passage
    finally:
        db.close()


def test_unrelated_query_does_not_fabricate_replacement_relevance():
    """不相关查询得到 no_relevant_policy，绝不伪造 replacement relevance。"""
    db = SessionLocal()
    try:
        intent = _replacement_intent(issue_summary="包裹物流配送查询，快递还没送到")
        result = retrieve_policy_passages(db, query_for_intent(intent))

        assert result.status is PolicyRetrievalStatus.NO_RELEVANT_POLICY
        assert result.passages == []
        assert result.failure_reason is not None
    finally:
        db.close()


def test_missing_corpus_is_explicit_retrieval_failure():
    """corpus 不存在时显式 corpus_unavailable，不产生任何 passage。"""
    db = SessionLocal()
    try:
        db.query(PolicyChunk).filter(PolicyChunk.is_demo_data == True).delete(
            synchronize_session=False
        )
        db.query(PolicyDocument).filter(PolicyDocument.is_demo_data == True).delete(
            synchronize_session=False
        )
        db.commit()

        result = retrieve_policy_passages(
            db, query_for_intent(_replacement_intent())
        )
        assert result.status is PolicyRetrievalStatus.CORPUS_UNAVAILABLE
        assert result.passages == []
        assert result.failure_reason is not None
    finally:
        db.close()


def test_multiple_chunks_rank_deterministically_and_repeatably():
    """multiple chunks 排序稳定，重复调用返回完全一致的结果。"""
    db = SessionLocal()
    try:
        query = query_for_intent(_replacement_intent())
        first = retrieve_policy_passages(db, query)
        second = retrieve_policy_passages(db, query)

        assert first.status is PolicyRetrievalStatus.SUCCESS
        assert len(first.passages) >= 2

        scores = [p.score for p in first.passages]
        ranks = [p.rank for p in first.passages]
        assert ranks == list(range(1, len(first.passages) + 1))
        # 分数非递增：排序由 score 降序 + chunk_order 稳定 tie-break 决定
        assert scores == sorted(scores, reverse=True)

        # 重复调用结果可重复：chunk_key 与 score 完全一致
        assert [p.chunk_key for p in first.passages] == [
            p.chunk_key for p in second.passages
        ]
        assert [p.score for p in first.passages] == [p.score for p in second.passages]
    finally:
        db.close()


def test_retrieved_passages_are_real_chunks_not_fabricated():
    """每条 retrieved passage 都对应数据库真实 chunk，passage 文本与来源一致。"""
    db = SessionLocal()
    try:
        result = retrieve_policy_passages(
            db, query_for_intent(_replacement_intent())
        )
        assert result.status is PolicyRetrievalStatus.SUCCESS

        document = (
            db.query(PolicyDocument)
            .filter(PolicyDocument.business_key == DEMO_DOC_KEY)
            .one()
        )
        chunks = {
            chunk.business_key: chunk
            for chunk in db.query(PolicyChunk)
            .filter(PolicyChunk.document_id == document.id)
            .all()
        }

        for passage in result.passages:
            real = chunks.get(passage.chunk_key)
            assert real is not None, f"passage 引用了不存在的 chunk {passage.chunk_key}"
            assert passage.passage == real.text
            assert passage.source_reference == real.source_reference
            assert passage.chunk_order == real.chunk_order
    finally:
        db.close()


# --------------------------------------------------------------------------
# malformed / unsupported / infrastructure
# --------------------------------------------------------------------------


def test_raw_model_text_is_malformed_query():
    """模型自由文本不是检索输入，返回 malformed_query，绝不进入成功路径。"""
    db = SessionLocal()
    try:
        result = retrieve_policy_passages(db, "模型说政策允许换货")
        assert result.status is PolicyRetrievalStatus.MALFORMED_QUERY
        assert result.passages == []
        assert result.failure_reason is not None
    finally:
        db.close()


def test_unmapped_intent_is_unsupported_query(monkeypatch):
    """未在 retrieval corpus 映射中登记的 intent 显式 unsupported。"""
    import policy_retrieval_service

    monkeypatch.setattr(policy_retrieval_service, "RETRIEVAL_CORPUS_KEYS", {})

    db = SessionLocal()
    try:
        result = retrieve_policy_passages(
            db, query_for_intent(_replacement_intent())
        )
        assert result.status is PolicyRetrievalStatus.UNSUPPORTED_QUERY
        assert result.passages == []
        assert result.failure_reason is not None
    finally:
        db.close()


def test_success_result_cannot_be_constructed_without_passages():
    """无 passage 却宣称检索成功在契约层无法构造。"""
    with pytest.raises(ValidationError):
        PolicyRetrievalResult(status=PolicyRetrievalStatus.SUCCESS)


# --------------------------------------------------------------------------
# decision layer 集成
# --------------------------------------------------------------------------


def test_retrieved_source_reaches_decision_layer():
    """retrieved source 到达 decision layer，deterministic facts 仍来自 lookup。"""
    db = SessionLocal()
    try:
        intent = _replacement_intent()
        retrieval = retrieve_policy_passages(db, query_for_intent(intent))
        assert retrieval.status is PolicyRetrievalStatus.SUCCESS

        policy = lookup_replacement_policy(db, intent)
        order = get_order(db, GetOrderRequest(order_key=LOW_RISK_ORDER_KEY))
        inventory = check_inventory(
            db, CheckInventoryRequest(product_sku=AVAILABLE_SKU)
        )

        decision = decide_replacement(
            IntentExtractionOutcome(
                status=IntentExtractionStatus.SUCCESS, intent=intent
            ),
            policy,
            order,
            inventory,
            retrieval=retrieval,
        )

        assert decision.status is ReplacementDecisionStatus.ELIGIBLE
        assert decision.evidence.policy.retrieved_source_references == [DEMO_SOURCE_REF]
        assert decision.evidence.policy.retrieved_document_keys == [DEMO_DOC_KEY]
        # deterministic facts 仍来自 application-owned structured lookup
        assert decision.evidence.policy.replacement_window_days == 30
        assert decision.evidence.policy.approval_required_above_amount is not None
    finally:
        db.close()


# --------------------------------------------------------------------------
# Agent Run persistence + Audit integration
# --------------------------------------------------------------------------


def test_policy_retrieval_is_persisted_as_inspectable_agent_step():
    """retrieval 结果以类型化字段落库，可被事后检查。"""
    agent_run = _run(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        run = db.query(AgentRun).filter(AgentRun.id == agent_run.id).one()
        assert run.status is AgentRunStatus.COMPLETED

        retrieval = run.policy_retrieval
        assert retrieval is not None
        assert retrieval.status == "success"
        assert retrieval.query_summary
        assert retrieval.document_key == DEMO_DOC_KEY
        assert retrieval.document_title == DEMO_DOC_TITLE
        assert retrieval.source_reference == DEMO_SOURCE_REF
        assert retrieval.is_demo_data is True
        assert retrieval.failure_reason is None

        passages = json.loads(retrieval.passages_json)
        assert isinstance(passages, list) and passages
        assert all("chunk_key" in p and "passage" in p for p in passages)

        # 每个 Run 至多一条检索记录
        assert (
            db.query(AgentPolicyRetrieval)
            .filter(AgentPolicyRetrieval.agent_run_id == run.id)
            .count()
            == 1
        )
    finally:
        db.close()


def test_policy_retrieval_audit_event_is_recorded():
    """T023 Audit Trail 记录 policy_retrieved 的 success 与真实 source。"""
    agent_run = _run(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        events = (
            db.query(AuditEvent)
            .filter(AuditEvent.agent_run_id == agent_run.id)
            .filter(AuditEvent.event_type == AuditEventType.POLICY_RETRIEVED)
            .all()
        )
        assert len(events) == 1
        event = events[0]
        assert event.outcome == "success"
        assert event.success is True
        assert event.affected_object_type == "agent_run"
        assert event.affected_object_key == agent_run.business_key
        assert DEMO_DOC_KEY in event.summary
    finally:
        db.close()


# --------------------------------------------------------------------------
# Golden Path 回归 + fail closed
# --------------------------------------------------------------------------


def test_retrieval_failure_fails_closed_without_replacement():
    """retrieval failure 进入安全 path：Run 失败，不创建换货单，不变成允许换货。"""
    db = SessionLocal()
    try:
        db.query(PolicyChunk).filter(PolicyChunk.is_demo_data == True).delete(
            synchronize_session=False
        )
        db.query(PolicyDocument).filter(PolicyDocument.is_demo_data == True).delete(
            synchronize_session=False
        )
        db.commit()
    finally:
        db.close()

    agent_run = _run(LOW_RISK_TICKET_KEY)

    db = SessionLocal()
    try:
        run = db.query(AgentRun).filter(AgentRun.id == agent_run.id).one()
        assert run.status is AgentRunStatus.FAILED
        assert "corpus_unavailable" in run.error_message
        assert db.query(ReplacementOrder).count() == 0

        # 失败被持久化，可检查
        assert run.policy_retrieval is not None
        assert run.policy_retrieval.status == "corpus_unavailable"
    finally:
        db.close()


def test_low_risk_golden_path_exposes_policy_basis():
    """low-risk 完成，响应暴露 policy basis，UI 身份与后端一致。"""
    response = client.post(f"/tickets/{LOW_RISK_TICKET_KEY}/agent-runs")
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "completed"

    basis = payload["policy_basis"]
    assert basis is not None
    assert basis["status"] == "success"
    assert basis["document_key"] == DEMO_DOC_KEY
    assert basis["document_title"] == DEMO_DOC_TITLE
    assert basis["source_reference"] == DEMO_SOURCE_REF
    assert basis["is_demo_data"] is True
    assert basis["failure_reason"] is None
    assert basis["passages"]
    assert basis["passages"][0]["chunk_key"].startswith(f"{DEMO_DOC_KEY}#chunk-")


def test_approval_required_golden_path_keeps_policy_basis_and_risk_gate():
    """high-risk：retrieval 成功、Risk Gate 仍拦截，policy basis 端到端一致。"""
    response = client.post(f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs")
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "waiting_for_approval"
    assert payload["replacement"] is None

    # Risk Gate 不被 retrieval 绕过：仍产生审批请求与风险原因
    assert payload["risk"] is not None
    assert payload["risk"]["rule_code"] == "order_amount_above_approval_threshold"
    assert payload["approval_request"] is not None

    # policy retrieval 仍成功并暴露真实来源
    basis = payload["policy_basis"]
    assert basis is not None
    assert basis["status"] == "success"
    assert basis["document_key"] == DEMO_DOC_KEY
    assert basis["source_reference"] == DEMO_SOURCE_REF


def test_resume_after_approval_preserves_policy_basis():
    """approval 后 resume 完成闭环，policy basis 仍保留真实来源。"""
    start = client.post(f"/tickets/{HIGH_VALUE_TICKET_KEY}/agent-runs")
    payload = start.json()
    run_key = payload["business_key"]
    approval_key = payload["approval_request"]["approval_key"]
    assert payload["policy_basis"]["document_key"] == DEMO_DOC_KEY

    approved = client.post(
        f"/approval-requests/{approval_key}/approve",
        json={"decision_reason": "金额核实无误，同意换货"},
    )
    assert approved.status_code == 200

    resume = client.post(f"/agent-runs/{run_key}/resume")
    assert resume.status_code == 200
    body = resume.json()
    assert body["agent_run"]["status"] == "completed"

    # resume 不破坏检索持久化：policy basis 仍一致
    assert body["agent_run"]["policy_basis"]["document_key"] == DEMO_DOC_KEY
    assert body["agent_run"]["policy_basis"]["source_reference"] == DEMO_SOURCE_REF
