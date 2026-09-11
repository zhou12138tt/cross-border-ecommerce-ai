"""路由聚合。"""
from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.customer_service import router as cs_router
from app.api.v1.knowledge_base import router as kb_router
from app.api.v1.market import router as market_router
from app.api.v1.recommendation import router as rec_router
from app.api.v1.supply_chain import router as sc_router

api_router = APIRouter()
api_router.include_router(market_router, prefix="/market", tags=["智能市场分析"])
api_router.include_router(cs_router, prefix="/customer-service", tags=["智能客服"])
api_router.include_router(sc_router, prefix="/supply-chain", tags=["智能供应链"])
api_router.include_router(rec_router, prefix="/recommendation", tags=["精准营销推荐"])
api_router.include_router(kb_router, prefix="/knowledge-base", tags=["知识库管理"])
