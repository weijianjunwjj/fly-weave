from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent

import pytest
from alembic import command
from alembic.config import Config

ALEMBIC_INI_PATH = API_DIR / "alembic.ini"
MIGRATIONS_PATH = API_DIR / "migrations"


@pytest.fixture(scope="session", autouse=True)
def apply_database_migrations(request):
    """
    在测试会话开始时，通过 Alembic 将 DATABASE_URL 指向的真实 PostgreSQL
    数据库迁移到最新版本（head）。

    使用绝对路径构造 Config，避免依赖 pytest 的当前工作目录。
    若数据库已处于最新迁移版本，Alembic 的 upgrade 是幂等操作，不会
    重复执行或报错。schema 初始化的权威路径始终是 Alembic 迁移脚本，
    而不是 Base.metadata.create_all 或任何手动准备。
    """
    # Pure configuration tests must remain runnable before a local PostgreSQL
    # instance exists. Database-backed test runs still migrate exactly once.
    if all(item.get_closest_marker("no_database") for item in request.session.items):
        return

    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    command.upgrade(config, "head")
