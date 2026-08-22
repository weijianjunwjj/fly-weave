from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_app_loads():
    """测试 FastAPI 应用能够加载"""
    assert app is not None
    assert app.title == "Flyweave API"


def test_health_endpoint_status_code():
    """测试健康检查端点返回 200"""
    response = client.get("/health")
    assert response.status_code == 200


def test_health_endpoint_structure():
    """测试健康检查返回结构化的 healthy 响应"""
    response = client.get("/health")
    data = response.json()

    assert "status" in data
    assert data["status"] == "healthy"

    assert "app_name" in data
    assert data["app_name"] == "Flyweave API"

    assert "environment" in data
    assert isinstance(data["environment"], str)
