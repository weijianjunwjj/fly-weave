from datetime import datetime

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
