"""Golden Path 的 intent 提案来源（T018）。

T011 已经确立了 intent 的 validation boundary：``extract_intent`` 把"一段原始
输出"转换成已验证的结构化 intent，或者一个结构化失败。它需要一个上游来提供
那段原始输出。当前系统尚未接入任何模型客户端，因此本模块在该位置提供一个
**确定性的、application-owned 的提案来源**，并把这个接缝显式化：

- 它不是模型，也不假装是模型。它只把工单**已经持久化的**事实重述成一份 intent
  候选，不做任何推测、不补齐数据库里没有的信息；
- 它产出的只是"候选"。候选必须原样穿过 T011 的 validation boundary 才可能成为
  已验证 intent —— 本模块没有任何途径把 intent 步骤直接置为成功；
- 工单若没有被应用标注为换货类场景，本模块返回 ``None``，intent 步骤会真实失败
  （``MODEL_FAILURE``），整条流程随即终止。这里不存在"总能提案成功"的兜底。

后续接入真实模型客户端时，替换本模块的实现即可，编排与下游 Tool 都无需改动。
"""
import json

from intents import IntentType, RequestedAction
from models import Ticket

# 与 CreateReplacementRequest.reason 的列宽保持一致：issue_summary 最终会成为
# 换货原因，超长文本在这里截断，而不是等到写入边界才失败
MAX_ISSUE_SUMMARY_LENGTH = 500

# 应用自己标注在工单上的换货类场景。这是当前 domain 中唯一持久化的工单分类，
# 因此也是唯一可以据以提案的依据；未登记的场景一律不提案。
REPLACEMENT_DEMO_SCENARIOS: frozenset[str] = frozenset(
    {"low_risk", "approval_required", "rejected"}
)

# 确定性提案的置信度。这不是模型的自述置信度，而是"本次提案没有猜测成分"这一
# 事实：候选的每个字段都直接来自持久化工单。后续换成真实模型时，该值必须改为
# 模型实际给出的置信度。
DETERMINISTIC_PROPOSAL_CONFIDENCE = 1.0


def propose_replacement_intent(ticket: Ticket) -> str | None:
    """把一张已持久化工单重述为 intent 候选的原始 JSON 文本。

    返回 ``None`` 表示"没有可提案的依据"，调用方据此会得到一次真实的 intent
    抽取失败，而不是一个被伪造出来的成功 intent。
    """
    if not isinstance(ticket, Ticket):
        return None
    if ticket.demo_scenario not in REPLACEMENT_DEMO_SCENARIOS:
        return None

    issue_summary = (ticket.description or "").strip()
    if not issue_summary:
        return None

    return json.dumps(
        {
            "intent_type": IntentType.QUALITY_ISSUE_REPLACEMENT.value,
            "requested_action": RequestedAction.REPLACEMENT.value,
            "issue_summary": issue_summary[:MAX_ISSUE_SUMMARY_LENGTH],
            "confidence": DETERMINISTIC_PROPOSAL_CONFIDENCE,
        },
        ensure_ascii=False,
    )
