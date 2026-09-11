"""FastAPI 应用入口。

启动方式：
    uvicorn app.main:app --reload --port 8000

访问：
    - Swagger 文档：http://localhost:8000/docs
    - ReDoc：       http://localhost:8000/redoc
    - 健康检查：     http://localhost:8000/health
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.logging import logger, setup_logging

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    logger.info(f"启动 {settings.APP_NAME} | env={settings.APP_ENV} | db={settings.DATABASE_URL}")
    yield
    logger.info("应用关闭")


app = FastAPI(
    title=settings.APP_NAME,
    description="AI 驱动的跨境电商运营平台（市场分析 / 智能客服 / 供应链 / 推荐）",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["系统"])
def health() -> dict:
    return {"status": "ok", "app": settings.APP_NAME, "env": settings.APP_ENV}


app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# 静态前端：启动后直接访问 http://localhost:8000/ 即聊天界面
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
