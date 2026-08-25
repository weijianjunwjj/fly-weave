"""get_order Tool 的 application / data query boundary。

Golden Path 的第一个真实业务数据 Tool：把一个已验证的 typed 请求解析为对
持久化 ``Order`` 记录的精确查询，再把数据库中真实存在的事实包装成 typed 结果。

    Agent / workflow
        → GetOrderRequest（已验证）
        → 本服务的仓储查询
        → 持久化的 Order
        → OrderLookupResult

成功与否完全由数据库查询结果决定。模型文本既不能成为查询输入，也不能把
``success`` 置为 true，更不能补齐任何缺失的订单字段。
"""
from sqlalchemy.orm import Session, joinedload

from models import Order
from orders import (
    GetOrderRequest,
    OrderFacts,
    OrderLookupResult,
    OrderLookupStatus,
)


def get_order(db: Session, request: GetOrderRequest) -> OrderLookupResult:
    """按订单业务标识查询真实持久化订单。

    输入必须是 ``GetOrderRequest``；任何非结构化输入（例如模型的原始输出字符串）
    都在 boundary 处被拒绝，返回 ``INVALID_REQUEST``，绝不进入查询与成功路径。
    """
    if not isinstance(request, GetOrderRequest):
        return OrderLookupResult(
            status=OrderLookupStatus.INVALID_REQUEST,
            failure_reason=(
                "get_order 只接受已验证的 GetOrderRequest，不接受模型原始文本"
            ),
        )

    order = (
        db.query(Order)
        .options(joinedload(Order.customer))
        .filter(Order.business_key == request.order_key)
        .one_or_none()
    )
    # 查不到就是查不到：返回结构化失败，不回退、不合成、不留空对象冒充成功
    if order is None:
        return OrderLookupResult(
            status=OrderLookupStatus.ORDER_NOT_FOUND,
            requested_order_key=request.order_key,
            failure_reason=f"未找到订单: {request.order_key}",
        )

    return OrderLookupResult(
        status=OrderLookupStatus.SUCCESS,
        requested_order_key=request.order_key,
        order=OrderFacts(
            order_key=order.business_key,
            # 客户引用同样使用稳定业务标识符，不对外暴露自增主键
            customer_key=order.customer.business_key,
            product_sku=order.product_sku,
            product_name=order.product_name,
            purchased_at=order.purchased_at,
            status=order.status,
            amount=order.amount,
            is_demo_data=order.is_demo_data,
        ),
    )
