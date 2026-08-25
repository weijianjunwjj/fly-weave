from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


API_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_FILE = API_DIR / ".env"


class Settings(BaseSettings):
    """环境驱动的应用配置"""

    app_name: str = "Flyweave API"
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str

    model_config = SettingsConfigDict(
        # Keep the local configuration contract independent from the process
        # working directory (pytest, Alembic, and FastAPI have different entry
        # points). Environment variables retain pydantic-settings precedence.
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
    )


settings = Settings()
