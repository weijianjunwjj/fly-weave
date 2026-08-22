from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """环境驱动的应用配置"""

    app_name: str = "Flyweave API"
    app_env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8"
    )


settings = Settings()
