"""应用配置 — 从 .env 文件和环境变量读取"""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "sqlineage"
    postgres_user: str = "sqlineage"
    postgres_password: str = ""

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0

    # App
    app_secret_key: str = "change_me"
    app_port: int = 8000
    app_debug: bool = True

    # AI (optional)
    llm_api_url: str = ""
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
