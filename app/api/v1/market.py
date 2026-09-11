"""智能市场分析 API。

算法 + LLM 双引擎：
    - 统计评分（销量×趋势/选品规则评分）
    - LLM 选品洞察报告：DeepSeek 基于 top-N 候选生成可读分析
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from app.api.v1.deps import get_session
from app.core.config import settings
from app.core.logging import logger
from app.db.models import Competitor, Product
from app.integrations.llm.deepseek import LLMError, chat_json

router = APIRouter()


# ---------- Schemas ----------
class ProductOut(BaseModel):
    id: int
    sku: str
    name: str
    category: str
    region: str
    price_usd: float
    sales_30d: int
    rating: float
    trend_score: float

    class Config:
        from_attributes = True


class TrendingItem(BaseModel):
    category: str
    region: str
    total_sales: int
    avg_trend: float
    score: float


class SelectRecommendation(BaseModel):
    product_id: int
    name: str
    category: str
    score: float
    reasons: List[str]


# ---------- Endpoints ----------
@router.get("/trending", response_model=List[TrendingItem], summary="热门品类洞察")
def trending(limit: int = 10, db: Session = Depends(get_session)):
    rows = (
        db.query(
            Product.category,
            Product.region,
            func.sum(Product.sales_30d).label("total_sales"),
            func.avg(Product.trend_score).label("avg_trend"),
        )
        .group_by(Product.category, Product.region)
        .all()
    )
    items = [
        TrendingItem(
            category=r.category,
            region=r.region,
            total_sales=int(r.total_sales or 0),
            avg_trend=round(float(r.avg_trend or 0), 2),
            score=round((r.total_sales or 0) * 0.6 + (r.avg_trend or 0) * 40, 2),
        )
        for r in rows
    ]
    items.sort(key=lambda x: x.score, reverse=True)
    return items[:limit]


@router.get("/products/{product_id}/competitors", summary="竞品对比")
def competitors(product_id: int, db: Session = Depends(get_session)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "商品不存在")
    comps = (
        db.query(Competitor)
        .filter(Competitor.product_id == product_id)
        .order_by(desc(Competitor.market_share))
        .all()
    )
    return {
        "product": ProductOut.model_validate(product).model_dump(),
        "competitors": [
            {
                "name": c.competitor_name,
                "price": c.competitor_price,
                "share": c.market_share,
                "price_gap_pct": round((c.competitor_price - product.price_usd) / product.price_usd * 100, 2),
            }
            for c in comps
        ],
    }


@router.get("/select-recommendations", response_model=List[SelectRecommendation], summary="AI 选品建议")
def select_recommendations(limit: int = 5, db: Session = Depends(get_session)):
    """评分 = 销量归一化 × 0.4 + 趋势归一化 × 0.3 + 评分归一化 × 0.2 - 竞品密度 × 0.1"""
    products = db.query(Product).all()
    if not products:
        return []

    sales_max = max(p.sales_30d for p in products) or 1
    trend_max = max(p.trend_score for p in products) or 1
    comp_count = {
        pid: cnt for pid, cnt in db.query(Competitor.product_id, func.count(Competitor.id)).group_by(Competitor.product_id).all()
    }
    comp_max = max(comp_count.values()) if comp_count else 1

    scored = []
    for p in products:
        sales_n = p.sales_30d / sales_max
        trend_n = p.trend_score / trend_max
        rating_n = (p.rating - 3.5) / 1.5  # 3.5~5.0 -> 0~1
        comp_density = comp_count.get(p.id, 0) / comp_max
        score = sales_n * 0.4 + trend_n * 0.3 + rating_n * 0.2 - comp_density * 0.1
        score = round(max(0, score) * 100, 2)
        reasons = []
        if sales_n > 0.7: reasons.append("近期销量高")
        if trend_n > 0.7: reasons.append("趋势得分强")
        if rating_n > 0.7: reasons.append("用户评分优秀")
        if comp_density < 0.3: reasons.append("竞争密度低，蓝海机会")
        scored.append(SelectRecommendation(
            product_id=p.id, name=p.name, category=p.category, score=score,
            reasons=reasons or ["综合表现平稳"],
        ))
    scored.sort(key=lambda x: x.score, reverse=True)
    return scored[:limit]


# ---------- LLM 选品洞察报告 ----------
class InsightReport(BaseModel):
    top_picks: list[SelectRecommendation]
    report: str
    source: str  # llm / rule


MARKET_SYSTEM_PROMPT = """你是跨境电商资深选品分析师。

任务：基于候选商品数据，生成一段简洁有洞察力的选品建议报告。

要求：
- 指出 top 商品的核心机会（销量/趋势/竞争维度）
- 识别可避开的红海品类
- 给出 1-2 条可执行建议
- 用中文输出，不超过 200 字
- 严格输出 JSON: {"report": "..."}"""


@router.get("/insight-report", response_model=InsightReport, summary="LLM 选品洞察报告")
def insight_report(top_n: int = 5, db: Session = Depends(get_session)):
    """对 top-N 选品候选生成 LLM 分析报告。"""
    recs = select_recommendations(limit=top_n, db=db)
    products = [db.get(Product, r.product_id) for r in recs]
    products = [p for p in products if p]

    report = ""
    source = "rule"
    if settings.llm_ready and products:
        items_text = "\n".join(
            f"- id={p.id} 名称={p.name} 品类={p.category} 区域={p.region} "
            f"价格=${p.price_usd} 销量30d={p.sales_30d} 评分={p.rating} 趋势={p.trend_score}"
            for p in products
        )
        try:
            data = chat_json(
                [{"role": "system", "content": MARKET_SYSTEM_PROMPT},
                 {"role": "user", "content": f"候选商品:\n{items_text}"}],
                temperature=0.5, max_tokens=400,
            )
            report = str(data.get("report", "")).strip()
            source = "llm"
            logger.info(f"LLM 选品报告生成成功 top_n={top_n}")
        except Exception as e:
            logger.warning(f"LLM 选品报告失败，降级: {e}")
    if not report:
        # 降级：规则文本
        lines = [f"基于综合评分，TOP{top_n} 选品候选："]
        for r in recs:
            lines.append(f"- {r.name}（{r.category}）评分 {r.score}：{', '.join(r.reasons)}")
        lines.append("建议优先关注评分>50 的品类，避开竞争密度高的红海。")
        report = "\n".join(lines)

    return InsightReport(top_picks=recs, report=report, source=source)
