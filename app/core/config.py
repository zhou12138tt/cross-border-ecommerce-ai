"""全局配置：基于 Pydantic Settings，从 .env 读取，所有值都有默认值保证零配置可跑。"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # 应用
    APP_NAME: str = "跨境电商AI平台"
    APP_ENV: Literal["development", "staging", "production"] = "development"
    APP_DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # 数据库（Demo 默认 SQLite，零依赖启动）
    DATABASE_URL: str = "sqlite:///./cbe_ai.db"

    # 缓存 / 队列
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_ENABLED: bool = False

    # LLM
    LLM_PROVIDER: Literal["mock", "openai", "qwen", "deepseek"] = "deepseek"
    OPENAI_API_KEY: str = ""
    QWEN_API_KEY: str = ""
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    @property
    def llm_ready(self) -> bool:
        """是否配置了可用的 LLM key（mock 视为未就绪）。"""
        if self.LLM_PROVIDER == "deepseek":
            return bool(self.DEEPSEEK_API_KEY)
        if self.LLM_PROVIDER == "openai":
            return bool(self.OPENAI_API_KEY)
        if self.LLM_PROVIDER == "qwen":
            return bool(self.QWEN_API_KEY)
        return False

    # 日志
    LOG_LEVEL: str = "INFO"

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def is_dev(self) -> bool:
        return self.APP_ENV == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
