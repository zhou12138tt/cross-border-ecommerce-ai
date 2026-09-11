"""基于 loguru 的统一日志配置。"""
from __future__ import annotations

import sys

from loguru import logger

from app.core.config import settings


def setup_logging() -> None:
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    if settings.APP_ENV != "development":
        logger.add(
            "logs/app_{time:YYYYMMDD}.log",
            rotation="00:00",
            retention="14 days",
            level=settings.LOG_LEVEL,
            encoding="utf-8",
        )


__all__ = ["logger", "setup_logging"]
