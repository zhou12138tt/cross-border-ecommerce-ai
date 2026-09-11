"""数据模型：覆盖四大业务模块的最小实体集。

仅用于 Demo，字段做了精简；生产可在此基础上加索引、约束、软删除等。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import ForeignKey, Integer, String, Float, DateTime, Text, JSON, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


# ---------- 市场分析 ----------
class Product(TimestampMixin, Base):
    """商品（含市场分析维度）。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(128), index=True)
    region: Mapped[str] = mapped_column(String(64))  # 目标市场，如 US / JP / DE
    price_usd: Mapped[float] = mapped_column(Float)
    sales_30d: Mapped[int] = mapped_column(Integer, default=0)
    rating: Mapped[float] = mapped_column(Float, default=4.5)
    trend_score: Mapped[float] = mapped_column(Float, default=0.0)  # 趋势打分 0-100


class Competitor(TimestampMixin, Base):
    """竞品快照。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    competitor_name: Mapped[str] = mapped_column(String(255))
    competitor_price: Mapped[float] = mapped_column(Float)
    market_share: Mapped[float] = mapped_column(Float, default=0.0)
    snapshot_date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ---------- 客服 ----------
class Customer(TimestampMixin, Base):
    """客户。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(64))
    language: Mapped[str] = mapped_column(String(16), default="en")
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class ChatSession(TimestampMixin, Base):
    """一次客服会话。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), index=True)
    language: Mapped[str] = mapped_column(String(16), default="en")
    status: Mapped[str] = mapped_column(String(16), default="open")  # open/closed
    messages: Mapped[list] = mapped_column(JSON, default=list)  # [{role,content,ts}]


# ---------- 供应链 ----------
class Warehouse(TimestampMixin, Base):
    """仓库。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    country: Mapped[str] = mapped_column(String(64))
    capacity_m3: Mapped[float] = mapped_column(Float)


class Inventory(TimestampMixin, Base):
    """库存。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    warehouse_id: Mapped[int] = mapped_column(ForeignKey("warehouse.id"), index=True)
    stock: Mapped[int] = mapped_column(Integer, default=0)
    safety_stock: Mapped[int] = mapped_column(Integer, default=10)
    in_transit: Mapped[int] = mapped_column(Integer, default=0)


class Shipment(TimestampMixin, Base):
    """物流订单。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    origin_country: Mapped[str] = mapped_column(String(64))
    dest_country: Mapped[str] = mapped_column(String(64))
    carrier: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    est_days: Mapped[int] = mapped_column(Integer, default=7)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)


# ---------- 推荐 ----------
class UserBehavior(TimestampMixin, Base):
    """用户行为日志（浏览/收藏/购买）。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("product.id"), index=True)
    action: Mapped[str] = mapped_column(String(32))  # view/favorite/purchase
    weight: Mapped[float] = mapped_column(Float, default=1.0)  # view=1/favorite=5/purchase=10


# ---------- 知识库 ----------
class KBEntry(TimestampMixin, Base):
    """客服知识库 FAQ 条目（可增删改查，运行时重建索引）。"""
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kb_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # 业务 ID，如 kb001
    category: Mapped[str] = mapped_column(String(64), index=True)
    question: Mapped[str] = mapped_column(Text)  # 检索文本（可含同义词扩展）
    answer: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
