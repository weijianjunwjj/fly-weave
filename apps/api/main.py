from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from config import settings
from database import get_db
from models import Ticket


app = FastAPI(title=settings.app_name)

# 允许本地前端开发服务器（Vite 默认端口）跨域访问健康检查等接口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


class HealthResponse(BaseModel):
    """健康检查响应模型"""
    status: str
    app_name: str
    environment: str


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """返回结构化的健康状态"""
    return HealthResponse(
        status="healthy",
        app_name=settings.app_name,
        environment=settings.app_env
    )


class TicketResponse(BaseModel):
    """工单只读响应模型"""
    business_key: str
    subject: str
    description: str
    status: str
    demo_scenario: str | None
    is_demo_data: bool
    created_at: datetime


@app.get("/tickets", response_model=list[TicketResponse])
async def list_tickets(db: Session = Depends(get_db)) -> list[TicketResponse]:
    """返回已持久化的工单，按创建时间排序"""
    tickets = db.query(Ticket).order_by(Ticket.created_at.asc()).all()
    return [
        TicketResponse(
            business_key=ticket.business_key,
            subject=ticket.subject,
            description=ticket.description,
            status=ticket.status.value,
            demo_scenario=ticket.demo_scenario,
            is_demo_data=ticket.is_demo_data,
            created_at=ticket.created_at,
        )
        for ticket in tickets
    ]


class CustomerContextResponse(BaseModel):
    """工单关联的客户上下文"""
    business_key: str
    name: str
    email: str
    phone: str | None
    is_demo_data: bool


class OrderContextResponse(BaseModel):
    """工单关联的订单 / 商品上下文"""
    business_key: str
    product_sku: str
    product_name: str
    purchased_at: datetime
    status: str
    # 金额以字符串返回，避免浮点精度损失并保持展示与数据库一致
    amount: str
    is_demo_data: bool


class TicketDetailResponse(BaseModel):
    """工单详情只读响应模型，含关联的客户与订单上下文"""
    business_key: str
    subject: str
    description: str
    status: str
    demo_scenario: str | None
    is_demo_data: bool
    created_at: datetime
    customer: CustomerContextResponse | None
    order: OrderContextResponse | None


@app.get("/tickets/{business_key}", response_model=TicketDetailResponse)
async def get_ticket_detail(
    business_key: str, db: Session = Depends(get_db)
) -> TicketDetailResponse:
    """按业务标识返回单个已持久化工单及其关联的客户 / 订单上下文"""
    ticket = (
        db.query(Ticket)
        .options(joinedload(Ticket.customer), joinedload(Ticket.order))
        .filter(Ticket.business_key == business_key)
        .one_or_none()
    )
    # 未知业务标识返回诚实的 404，不提供任何回退或合成数据
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"未找到工单: {business_key}")

    customer = ticket.customer
    order = ticket.order

    return TicketDetailResponse(
        business_key=ticket.business_key,
        subject=ticket.subject,
        description=ticket.description,
        status=ticket.status.value,
        demo_scenario=ticket.demo_scenario,
        is_demo_data=ticket.is_demo_data,
        created_at=ticket.created_at,
        customer=CustomerContextResponse(
            business_key=customer.business_key,
            name=customer.name,
            email=customer.email,
            phone=customer.phone,
            is_demo_data=customer.is_demo_data,
        )
        if customer is not None
        else None,
        order=OrderContextResponse(
            business_key=order.business_key,
            product_sku=order.product_sku,
            product_name=order.product_name,
            purchased_at=order.purchased_at,
            status=order.status.value,
            amount=str(order.amount),
            is_demo_data=order.is_demo_data,
        )
        if order is not None
        else None,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
