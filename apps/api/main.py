from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import settings


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=True
    )
