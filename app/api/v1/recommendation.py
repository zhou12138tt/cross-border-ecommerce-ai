"""精准营销与个性化推荐 API。

实现：
    - 协同过滤召回（item-based 共现矩阵）
    - LLM 生成个性化推荐理由 / 商品营销描述（DeepSeek）
    - 冷启动 fallback（热销）
"""
from __future__ import annotations

from collections import defaultdict
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.deps import get_session
from app.core.config import settings
from app.core.logging import logger
from app.db.models import Product, UserBehavior
from app.integrations.llm.deepseek import LLMError, chat_json

router = APIRouter()


# ---------- Schemas ----------
class RecommendationItem(BaseModel):
    product_id: int
    name: str
    category: str
    price_usd: float
    score: float


class RecommendationWithDesc(RecommendationItem):
    description: Optional[str] = None  # LLM 生成的推荐理由


class CampaignSuggestion(BaseModel):
    customer_id: int
    segment: str
    campaign: str
    discount_pct: int


class ProductDescription(BaseModel):
    product_id: int
    name: str
    category: str
    price_usd: float
    description: str
    language: str


# ---------- 协同过滤 ----------
def _build_cooccur(db: Session) -> dict[int, dict[int, float]]:
    """构建商品-商品共现矩阵（基于同一客户的行为加权）。"""
    user_items: dict[int, dict[int, float]] = defaultdict(dict)
    for b in db.query(UserBehavior).all():
        user_items[b.customer_id][b.product_id] = user_items[b.customer_id].get(b.product_id, 0) + b.weight

    co: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
    for items in user_items.values():
        pids = list(items.keys())
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                co[pids[i]][pids[j]] += items[pids[i]] * items[pids[j]]
                co[pids[j]][pids[i]] += items[pids[i]] * items[pids[j]]
    return {k: dict(v) for k, v in co.items()}


# ---------- LLM 商品描述/推荐理由 ----------
DESC_SYSTEM_PROMPT = """你是一个跨境电商平台的营销文案专家。

任务：基于商品信息和客户画像，用指定语言生成一段有吸引力的推荐理由。

要求：
- 使用指定客户语言
- 突出商品卖点 + 与客户偏好的契合点
- 简洁有感染力（30-60 字）
- 严格输出 JSON: {"descriptions": [{"product_id": 1, "description": "..."}, ...]}"""


def _generate_descriptions(
    products: list[Product], customer_id: int, language: str, db: Session
) -> dict[int, str]:
    """批量生成推荐理由。一次 LLM 调用，返回 {product_id: description}。

    失败时返回空 dict，上层用 None 兜底。
    """
    if not settings.llm_ready:
        return {}

    # 客户画像：近期行为品类偏好
    behaviors = db.query(UserBehavior).filter(UserBehavior.customer_id == customer_id).all()
    pref_cats: dict[str, int] = defaultdict(int)
    for b in behaviors:
        p = db.get(Product, b.product_id)
        if p:
            pref_cats[p.category] += int(b.weight)
    top_cats = sorted(pref_cats.items(), key=lambda x: x[1], reverse=True)[:3]
    pref_text = "、".join(c for c, _ in top_cats) if top_cats else "无明显偏好"

    lang_name = {"en": "English", "ja": "日本語", "de": "Deutsch", "ar": "العربية"}.get(language, "English")
    items_text = "\n".join(
        f"- id={p.id} 名称={p.name} 品类={p.category} 价格=${p.price_usd} 评分={p.rating} 30天销量={p.sales_30d}"
        for p in products
    )
    user_prompt = (
        f"客户语言: {lang_name}\n"
        f"客户偏好品类: {pref_text}\n"
        f"待推荐商品:\n{items_text}\n"
        f"请为每个商品生成推荐理由，输出 JSON。"
    )
    try:
        data = chat_json(
            [{"role": "system", "content": DESC_SYSTEM_PROMPT},
             {"role": "user", "content": user_prompt}],
            temperature=0.6, max_tokens=600,
        )
        out: dict[int, str] = {}
        for item in data.get("descriptions", []):
            pid = item.get("product_id")
            desc = str(item.get("description", "")).strip()
            if pid and desc:
                out[int(pid)] = desc
        logger.info(f"LLM 生成 {len(out)} 条推荐理由 lang={language}")
        return out
    except Exception as e:
        logger.warning(f"LLM 推荐理由生成失败: {e}")
        return {}


# ---------- Endpoints ----------
@router.get("/users/{customer_id}/recommendations",
            response_model=List[RecommendationItem], summary="个性化推荐")
def recommend(customer_id: int, top_k: int = 5,
             with_description: bool = False, language: str = "en",
             db: Session = Depends(get_session)):
    behaviors = db.query(UserBehavior).filter(UserBehavior.customer_id == customer_id).all()
    if not behaviors:
        # 冷启动：返回全局热销
        popular = db.query(Product).order_by(Product.sales_30d.desc()).limit(top_k).all()
        return [RecommendationItem(
            product_id=p.id, name=p.name, category=p.category,
            price_usd=p.price_usd, score=float(p.sales_30d),
        ) for p in popular]

    co = _build_cooccur(db)
    user_pids = {b.product_id: b.weight for b in behaviors}

    scores: dict[int, float] = defaultdict(float)
    for pid, w in user_pids.items():
        for related, sim in co.get(pid, {}).items():
            if related in user_pids:
                continue
            scores[related] += w * sim

    if not scores:
        popular = db.query(Product).order_by(Product.sales_30d.desc()).limit(top_k).all()
        return [RecommendationItem(
            product_id=p.id, name=p.name, category=p.category,
            price_usd=p.price_usd, score=float(p.sales_30d),
        ) for p in popular]

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    ids = [pid for pid, _ in ranked]
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(ids)).all()}
    out = []
    for pid, score in ranked:
        p = products.get(pid)
        if p:
            out.append(RecommendationItem(
                product_id=p.id, name=p.name, category=p.category,
                price_usd=p.price_usd, score=round(score, 2),
            ))
    return out


@router.get("/users/{customer_id}/recommendations-with-desc",
            response_model=List[RecommendationWithDesc], summary="个性化推荐（含 LLM 生成推荐理由）")
def recommend_with_desc(customer_id: int, top_k: int = 5, language: str = "en",
                         db: Session = Depends(get_session)):
    """推荐 + LLM 生成的个性化推荐理由（一次调用批量生成）。"""
    # 复用召回逻辑
    behaviors = db.query(UserBehavior).filter(UserBehavior.customer_id == customer_id).all()
    if not behaviors:
        popular = db.query(Product).order_by(Product.sales_30d.desc()).limit(top_k).all()
        items = [RecommendationWithDesc(
            product_id=p.id, name=p.name, category=p.category,
            price_usd=p.price_usd, score=float(p.sales_30d),
        ) for p in popular]
    else:
        co = _build_cooccur(db)
        user_pids = {b.product_id: b.weight for b in behaviors}
        scores: dict[int, float] = defaultdict(float)
        for pid, w in user_pids.items():
            for related, sim in co.get(pid, {}).items():
                if related in user_pids:
                    continue
                scores[related] += w * sim
        if not scores:
            popular = db.query(Product).order_by(Product.sales_30d.desc()).limit(top_k).all()
            items = [RecommendationWithDesc(
                product_id=p.id, name=p.name, category=p.category,
                price_usd=p.price_usd, score=float(p.sales_30d),
            ) for p in popular]
        else:
            ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
            ids = [pid for pid, _ in ranked]
            products_map = {p.id: p for p in db.query(Product).filter(Product.id.in_(ids)).all()}
            items = []
            for pid, score in ranked:
                p = products_map.get(pid)
                if p:
                    items.append(RecommendationWithDesc(
                        product_id=p.id, name=p.name, category=p.category,
                        price_usd=p.price_usd, score=round(score, 2),
                    ))

    # LLM 批量生成推荐理由
    if items:
        products_list = [db.get(Product, i.product_id) for i in items]
        products_list = [p for p in products_list if p]
        desc_map = _generate_descriptions(products_list, customer_id, language, db)
        for it in items:
            it.description = desc_map.get(it.product_id)
    return items


@router.get("/products/{product_id}/description",
            response_model=ProductDescription, summary="LLM 生成商品营销描述")
def product_description(product_id: int, language: str = "en",
                         db: Session = Depends(get_session)):
    """为单个商品生成多语言营销描述。"""
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(404, "商品不存在")

    lang_name = {"en": "English", "ja": "日本語", "de": "Deutsch", "ar": "العربية"}.get(language, "English")
    sys_prompt = "你是跨境电商营销文案专家。基于商品信息用指定语言生成一段有吸引力的营销描述（40-80字）。严格输出 JSON: {\"description\": \"...\"}"
    user_prompt = (
        f"商品: {p.name}\n品类: {p.category}\n价格: ${p.price_usd}\n"
        f"评分: {p.rating}\n30天销量: {p.sales_30d}\n语言: {lang_name}"
    )
    desc = ""
    if settings.llm_ready:
        try:
            data = chat_json(
                [{"role": "system", "content": sys_prompt},
                 {"role": "user", "content": user_prompt}],
                temperature=0.7, max_tokens=200,
            )
            desc = str(data.get("description", "")).strip()
        except Exception as e:
            logger.warning(f"LLM 商品描述生成失败: {e}")
    if not desc:
        desc = f"{p.name} - 来自{p.category}品类的高品质好物，仅需 ${p.price_usd}，已有大量买家好评。"

    return ProductDescription(
        product_id=p.id, name=p.name, category=p.category,
        price_usd=p.price_usd, description=desc, language=language,
    )


@router.get("/marketing/campaigns/{customer_id}",
            response_model=CampaignSuggestion, summary="个性化营销活动")
def campaign(customer_id: int, db: Session = Depends(get_session)):
    behaviors = db.query(UserBehavior).filter(UserBehavior.customer_id == customer_id).all()
    if not behaviors:
        return CampaignSuggestion(
            customer_id=customer_id, segment="新用户",
            campaign="新人专享：首单满 $50 减 $10", discount_pct=15,
        )

    purchase_count = sum(1 for b in behaviors if b.action == "purchase")
    avg_w = sum(b.weight for b in behaviors) / len(behaviors)

    if purchase_count == 0:
        segment, campaign, disc = "浏览未购", "限时优惠券：48 小时内下单立减", 12
    elif purchase_count >= 3 or avg_w > 6:
        segment, campaign, disc = "高价值复购客户", "会员专享：满 $100 减 $25 + 免运费", 20
    elif avg_w < 2:
        segment, campaign, disc = "低活跃客户", "召回红包：无门槛 $5 券", 8
    else:
        segment, campaign, disc = "常规客户", "阶梯满减：满 $80 减 $12", 12

    return CampaignSuggestion(
        customer_id=customer_id, segment=segment,
        campaign=campaign, discount_pct=disc,
    )
