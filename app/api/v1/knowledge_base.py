"""知识库管理 API。

提供 FAQ 的增删改查 + 重建索引 + 检索测试。
客服对话会自动从 DB 加载并检索该知识库。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.deps import get_session
from app.db.models import KBEntry
from app.integrations.llm.knowledge_base import reload_kb, retrieve

router = APIRouter()


# ---------- Schemas ----------
class KBEntryCreate(BaseModel):
    kb_id: str
    category: str
    question: str
    answer: str
    enabled: bool = True


class KBEntryUpdate(BaseModel):
    category: Optional[str] = None
    question: Optional[str] = None
    answer: Optional[str] = None
    enabled: Optional[bool] = None


class KBEntryOut(BaseModel):
    id: int
    kb_id: str
    category: str
    question: str
    answer: str
    enabled: bool

    class Config:
        from_attributes = True


class RetrieveHit(BaseModel):
    id: str
    category: str
    question: str
    answer: str
    score: float


# ---------- Endpoints ----------
@router.get("", response_model=List[KBEntryOut], summary="知识库列表")
def list_kb(
    category: Optional[str] = Query(None, description="按类别过滤"),
    keyword: Optional[str] = Query(None, description="问题/答案关键词搜索"),
    db: Session = Depends(get_session),
):
    q = db.query(KBEntry)
    if category:
        q = q.filter(KBEntry.category == category)
    if keyword:
        kw = f"%{keyword}%"
        q = q.filter(KBEntry.question.like(kw) | KBEntry.answer.like(kw))
    return q.order_by(KBEntry.id.desc()).all()


@router.get("/{kb_id}", response_model=KBEntryOut, summary="获取单条 FAQ")
def get_kb(kb_id: str, db: Session = Depends(get_session)):
    entry = db.query(KBEntry).filter(KBEntry.kb_id == kb_id).first()
    if not entry:
        raise HTTPException(404, "FAQ 不存在")
    return entry


@router.post("", response_model=KBEntryOut, summary="创建 FAQ")
def create_kb(req: KBEntryCreate, db: Session = Depends(get_session)):
    if db.query(KBEntry).filter(KBEntry.kb_id == req.kb_id).first():
        raise HTTPException(400, f"kb_id={req.kb_id} 已存在")
    entry = KBEntry(**req.model_dump())
    db.add(entry)
    db.commit()
    db.refresh(entry)
    reload_kb()  # 重建索引
    return entry


@router.put("/{kb_id}", response_model=KBEntryOut, summary="更新 FAQ")
def update_kb(kb_id: str, req: KBEntryUpdate, db: Session = Depends(get_session)):
    entry = db.query(KBEntry).filter(KBEntry.kb_id == kb_id).first()
    if not entry:
        raise HTTPException(404, "FAQ 不存在")
    data = req.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(entry, k, v)
    db.commit()
    db.refresh(entry)
    reload_kb()
    return entry


@router.delete("/{kb_id}", summary="删除 FAQ")
def delete_kb(kb_id: str, db: Session = Depends(get_session)):
    entry = db.query(KBEntry).filter(KBEntry.kb_id == kb_id).first()
    if not entry:
        raise HTTPException(404, "FAQ 不存在")
    db.delete(entry)
    db.commit()
    reload_kb()
    return {"kb_id": kb_id, "deleted": True}


@router.get("/retrieve/test", response_model=List[RetrieveHit], summary="检索测试")
def retrieve_test(q: str = Query(..., description="测试 query"), top_k: int = 3):
    """不经过 LLM，直接返回 TF-IDF 检索结果，便于调试知识库质量。"""
    return retrieve(q, top_k=top_k)


@router.post("/reload", summary="重建索引")
def rebuild_index():
    """知识库内容变更后，强制清缓存重建 TF-IDF 索引。

    注意：CRUD 端点已自动调用，本端点用于手动刷新或兜底。
    """
    count = reload_kb()
    return {"status": "reloaded", "entries": count}
