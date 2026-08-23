from pathlib import Path

from dotenv import load_dotenv

# 必须在任何应用模块（config / database / main）被导入之前加载 .env。
# config.Settings() 与 database.create_engine() 都在模块导入期执行，
# 若此时 DATABASE_URL 尚未就绪，pytest 会在收集阶段直接失败。
# 这里用绝对路径加载 apps/api/.env，避免依赖 pytest 的当前工作目录。
API_DIR = Path(__file__).resolve().parent.parent
load_dotenv(API_DIR / ".env")

import pytest
from alembic import command
from alembic.config import Config

ALEMBIC_INI_PATH = API_DIR / "alembic.ini"
MIGRATIONS_PATH = API_DIR / "migrations"


@pytest.fixture(scope="session", autouse=True)
def apply_database_migrations():
    """
    在测试会话开始时，通过 Alembic 将 DATABASE_URL 指向的真实 PostgreSQL
    数据库迁移到最新版本（head）。

    使用绝对路径构造 Config，避免依赖 pytest 的当前工作目录。
    若数据库已处于最新迁移版本，Alembic 的 upgrade 是幂等操作，不会
    重复执行或报错。schema 初始化的权威路径始终是 Alembic 迁移脚本，
    而不是 Base.metadata.create_all 或任何手动准备。
    """
    config = Config(str(ALEMBIC_INI_PATH))
    config.set_main_option("script_location", str(MIGRATIONS_PATH))
    command.upgrade(config, "head")
