import os

import pytest
from sqlalchemy import text

from database import engine


def test_database_url_is_configured():
    """验证 DATABASE_URL 环境变量已配置"""
    database_url = os.getenv("DATABASE_URL")
    assert database_url is not None, "DATABASE_URL 环境变量未设置"
    assert database_url.startswith("postgresql://"), "DATABASE_URL 必须是 PostgreSQL 连接字符串"


def test_database_connectivity():
    """对真实 PostgreSQL 执行连接性烟雾测试"""
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1 AS connectivity_check"))
        row = result.fetchone()
        assert row is not None
        assert row[0] == 1
