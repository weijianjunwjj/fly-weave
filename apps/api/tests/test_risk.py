from decimal import Decimal

import pytest

from risk import RiskLevel, RiskRuleCode
from risk_service import evaluate_replacement_risk


pytestmark = pytest.mark.no_database


def test_amount_below_threshold_is_low_risk():
    risk = evaluate_replacement_risk(
        order_key="ORD-LOW",
        order_amount=Decimal("499.99"),
        policy_key="POLICY-1",
        approval_threshold_amount=Decimal("500.00"),
    )

    assert risk.level is RiskLevel.LOW
    assert risk.rule_code is RiskRuleCode.NO_RULE_TRIGGERED
    assert risk.requires_approval is False


def test_amount_equal_to_threshold_does_not_require_approval():
    risk = evaluate_replacement_risk(
        order_key="ORD-EQUAL",
        order_amount=Decimal("500.00"),
        policy_key="POLICY-1",
        approval_threshold_amount=Decimal("500.00"),
    )

    assert risk.level is RiskLevel.LOW
    assert risk.requires_approval is False


def test_amount_above_threshold_requires_approval():
    risk = evaluate_replacement_risk(
        order_key="ORD-HIGH",
        order_amount=Decimal("500.01"),
        policy_key="POLICY-1",
        approval_threshold_amount=Decimal("500.00"),
    )

    assert risk.level is RiskLevel.HIGH
    assert (
        risk.rule_code
        is RiskRuleCode.ORDER_AMOUNT_ABOVE_APPROVAL_THRESHOLD
    )
    assert risk.requires_approval is True
    assert risk.order_amount == Decimal("500.01")
    assert risk.approval_threshold_amount == Decimal("500.00")


def test_policy_without_threshold_has_no_risk_rule():
    risk = evaluate_replacement_risk(
        order_key="ORD-NO-THRESHOLD",
        order_amount=Decimal("9999.00"),
        policy_key="POLICY-1",
        approval_threshold_amount=None,
    )

    assert risk.level is RiskLevel.LOW
    assert risk.rule_code is RiskRuleCode.NO_RULE_TRIGGERED
    assert risk.requires_approval is False
