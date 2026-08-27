"""受保护业务动作的确定性风险规则求值（T019）。

    ReplacementDecision（T015 的判定与其证据）
        → assess_replacement_risk
        → RiskAssessment（级别 + 规则码 + 可展示原因 + 规则依据）

当前只有一条风险规则：

    **订单金额 > 售后政策显式规定的人工审批金额阈值 ⇒ 需要人工审批。**

这条规则完全由应用拥有，且只读取两个既有的结构化业务事实：``Order.amount``
与 ``AfterSalesPolicy.approval_required_above_amount``。执行闸门读取当前持久化
事实，只读接口也从持久化状态重新求值。两者都是 ``Decimal``，比较是精确的。

因此本模块是纯确定性的：不调用模型、不接受自由文本、不读取当前时间、不使用
随机数。同一组输入必然得到同一个 ``RiskAssessment``，包括其中的原因文本。

本模块只回答"这次执行是否必须先经过人工审批"。它不创建审批对象、不记录审批
历史、不执行 approve / reject —— 这些都不在 T019 范围内。
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from decisions import ReplacementDecision
from intents import IntentType, RequestedAction
from models import AfterSalesPolicy, AgentIntent, AgentRun, Order
from policies import SUPPORTED_POLICY_KEYS
from risk import ProtectedAction, RiskAssessment, RiskLevel, RiskRuleCode


def assess_replacement_risk(
    db: Session, decision: ReplacementDecision, order: Order
) -> RiskAssessment | None:
    """用执行时刻的持久化事实，对换货执行做确定性风险判断。

    T015 判定只提供本次执行引用的政策标识；金额与审批阈值重新从当前数据库实体
    读取。这样即使 typed 判定在形成后已经陈旧或被调用方篡改，也不能用旧金额或
    旧阈值绕过保护动作前的最后一道闸门。

    政策已经不存在时返回 ``None``，由 mutation boundary fail-closed；缺少风险
    依据绝不能被解释为低风险放行。
    """
    try:
        query_key = (
            IntentType(decision.evidence.intent.intent_type),
            RequestedAction(decision.evidence.intent.requested_action),
        )
    except (TypeError, ValueError):
        return None
    policy_key = SUPPORTED_POLICY_KEYS.get(query_key)
    if policy_key is None or decision.evidence.policy.policy_key != policy_key:
        return None
    policy = (
        db.query(AfterSalesPolicy)
        .filter(AfterSalesPolicy.business_key == policy_key)
        .one_or_none()
    )
    if policy is None:
        return None

    return evaluate_replacement_risk(
        order_key=order.business_key,
        order_amount=order.amount,
        policy_key=policy.business_key,
        approval_threshold_amount=policy.approval_required_above_amount,
    )


def evaluate_replacement_risk(
    *,
    order_key: str | None,
    order_amount: Decimal | None,
    policy_key: str | None,
    approval_threshold_amount: Decimal | None,
) -> RiskAssessment:
    """对换货执行求值全部风险规则。

    这是规则的唯一定义处：无论输入来自执行闸门，还是来自事后读回的持久化
    状态，都走这个函数；同一组输入会得到同一个风险结论。
    """
    # --- 规则 1：订单金额超过政策规定的人工审批阈值 ---
    # 政策没有设阈值、或缺少订单金额时，这条规则无从求值，因此不命中；缺证据
    # 绝不被当作"已通过风险审查"以外的任何东西——它就是没有规则命中。
    if (
        order_amount is not None
        and approval_threshold_amount is not None
        and order_amount > approval_threshold_amount
    ):
        return RiskAssessment(
            action=ProtectedAction.CREATE_REPLACEMENT,
            level=RiskLevel.HIGH,
            rule_code=RiskRuleCode.ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD,
            requires_approval=True,
            reason=(
                f"订单 {order_key} 金额 {order_amount} 超过售后政策 "
                f"{policy_key} 规定的人工审批金额阈值 "
                f"{approval_threshold_amount}，创建换货单前必须经过人工审批。"
            ),
            order_key=order_key,
            order_amount=order_amount,
            approval_threshold_amount=approval_threshold_amount,
            policy_key=policy_key,
        )

    if approval_threshold_amount is None:
        return _no_rule_triggered(
            reason="售后政策未规定人工审批金额阈值，未命中任何风险规则",
            order_key=order_key,
            order_amount=order_amount,
            policy_key=policy_key,
        )

    return _no_rule_triggered(
        reason=(
            f"订单 {order_key} 金额 {order_amount} 未超过售后政策 "
            f"{policy_key} 规定的人工审批金额阈值 "
            f"{approval_threshold_amount}，未命中任何风险规则"
        ),
        order_key=order_key,
        order_amount=order_amount,
        approval_threshold_amount=approval_threshold_amount,
        policy_key=policy_key,
    )


def assess_persisted_replacement_risk(
    db: Session, agent_run: AgentRun
) -> RiskAssessment | None:
    """从持久化状态重新求值同一条规则，供只读接口把风险原因交给 UI。

    这不是第二套规则：重新求值仍走 ``evaluate_replacement_risk``。但这里读取的
    是查询时刻的当前数据库事实；若订单金额或政策阈值后来被修改，结果可能不同于
    当初拦截时刻。T019 暂不引入历史 snapshot，后续由 ApprovalRequest / Audit
    持久化执行时证据。

    缺少订单或政策依据时返回 ``None``，由调用方如实表示为"没有风险判断可展示"，
    而不是编造一个低风险结论。
    """
    ticket = agent_run.ticket
    order = ticket.order if ticket is not None else None
    if order is None:
        return None

    policy = _persisted_policy(db, agent_run.intent)
    if policy is None:
        return None

    return evaluate_replacement_risk(
        order_key=order.business_key,
        order_amount=order.amount,
        policy_key=policy.business_key,
        approval_threshold_amount=policy.approval_required_above_amount,
    )


def _persisted_policy(
    db: Session, agent_intent: AgentIntent | None
) -> AfterSalesPolicy | None:
    """根据 Run 的持久化 intent 与当前映射取回售后政策。

    T019 尚未持久化执行时的政策 snapshot；这里只沿用 T012 的 application-owned
    映射，并读取查询时刻的当前政策行，不做任何猜测或回退。
    """
    if agent_intent is None:
        return None
    try:
        query_key = (
            IntentType(agent_intent.intent_type),
            RequestedAction(agent_intent.requested_action),
        )
    except ValueError:
        return None

    policy_key = SUPPORTED_POLICY_KEYS.get(query_key)
    if policy_key is None:
        return None
    return (
        db.query(AfterSalesPolicy)
        .filter(AfterSalesPolicy.business_key == policy_key)
        .one_or_none()
    )


def _no_rule_triggered(
    *,
    reason: str,
    order_key: str | None = None,
    order_amount: Decimal | None = None,
    approval_threshold_amount: Decimal | None = None,
    policy_key: str | None = None,
) -> RiskAssessment:
    """构造"未命中任何风险规则"的低风险判断，并保留求值时看到的业务输入。"""
    return RiskAssessment(
        action=ProtectedAction.CREATE_REPLACEMENT,
        level=RiskLevel.LOW,
        rule_code=RiskRuleCode.NO_RULE_TRIGGERED,
        requires_approval=False,
        reason=reason,
        order_key=order_key,
        order_amount=order_amount,
        approval_threshold_amount=approval_threshold_amount,
        policy_key=policy_key,
    )
