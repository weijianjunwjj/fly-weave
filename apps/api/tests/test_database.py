import os

import pytest
from sqlalchemy import make_url, text

from database import engine


def test_database_url_is_configured():
    """验证 DATABASE_URL 环境变量已配置且指向 PostgreSQL"""
    database_url = os.getenv("DATABASE_URL")
    assert database_url is not None, "DATABASE_URL 环境变量未设置"
    # 用 SQLAlchemy 解析后比较后端名，兼容 postgresql:// 与 postgresql+psycopg2://
    # 等合法写法，同时仍会拒绝 sqlite / mysql 等非 PostgreSQL 数据库
    backend_name = make_url(database_url).get_backend_name()
    assert backend_name == "postgresql", (
        f"DATABASE_URL 必须是 PostgreSQL 连接字符串，实际后端: {backend_name}"
    )


def test_database_connectivity():
    """对真实 PostgreSQL 执行连接性烟雾测试"""
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1 AS connectivity_check"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 1
