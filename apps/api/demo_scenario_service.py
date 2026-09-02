"""Development-only bootstrap for the real High-Risk Replacement journey.

This module creates only the customer, order, and ticket inputs. The caller must
start the normal Agent Run endpoint; approvals, execution outcomes, and audit
events are produced exclusively by the existing production domain flow.
"""
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from models import Ticket
from ticket_intake_service import create_ticket


HIGH_RISK_REPLACEMENT = "high_risk_replacement"


def create_high_risk_replacement_demo(db: Session) -> Ticket:
    """Append a unique, policy-eligible high-value replacement request."""

    now = datetime.utcnow()
    unique = f"{int(now.timestamp() * 1000)}-{uuid4().hex[:8]}"
    return create_ticket(
        db,
        customer_name="演示客户",
        customer_email=f"demo-{unique}@flyweave.example",
        issue_type="换货",
        issue_description="商品存在质量问题，客户申请同款换货。",
        order_key=f"demo-order-{unique}",
        order_amount=Decimal("1299.00"),
        is_demo_data=True,
        demo_scenario=HIGH_RISK_REPLACEMENT,
    )
