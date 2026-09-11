"""向量知识库（RAG）。

实现：
    - 数据源：优先从 SQLite（KBEntry 表）加载，DB 空时回落到内置 kb_data.json
    - 索引：TF-IDF + 余弦相似度，@lru_cache 缓存
    - 重建：管理端点调用 reload_kb() 清缓存后下次检索重建

可平滑替换为 sentence-transformers / OpenAI embedding（仅替换 _build_index 内部）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from app.core.logging import logger

KB_PATH = Path(__file__).resolve().parent / "kb_data.json"


def _load_from_db() -> list[dict[str, Any]]:
    """从 SQLite 加载 KBEntry。失败/空表时返回 []。"""
    try:
        from app.db.session import SessionLocal
        from app.db.models import KBEntry
    except Exception as e:
        logger.warning(f"加载 DB 知识库失败: {e}")
        return []
    db = SessionLocal()
    try:
        rows = db.query(KBEntry).filter(KBEntry.enabled.is_(True)).all()
        return [
            {"id": r.kb_id, "category": r.category,
             "question": r.question, "answer": r.answer}
            for r in rows
        ]
    finally:
        db.close()


def _load_from_file() -> list[dict[str, Any]]:
    """从 kb_data.json 加载（内置兜底）。"""
    with open(KB_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@lru_cache
def _load_kb() -> list[dict[str, Any]]:
    """加载知识库：优先 DB，空则回落文件。"""
    docs = _load_from_db()
    if docs:
        logger.info(f"知识库加载完成（DB）：{len(docs)} 条 FAQ")
        return docs
    docs = _load_from_file()
    logger.info(f"知识库加载完成（文件兜底）：{len(docs)} 条 FAQ")
    return docs


@lru_cache
def _build_index() -> tuple[TfidfVectorizer, np.ndarray, list[dict[str, Any]]]:
    """构建 TF-IDF 索引。"""
    docs = _load_kb()
    corpus = [d["question"] + " " + d["category"] for d in docs]
    vectorizer = TfidfVectorizer(ngram_range=(1, 2))
    matrix = vectorizer.fit_transform(corpus)
    return vectorizer, matrix, docs


def retrieve(query: str, top_k: int = 3, min_score: float = 0.05) -> list[dict[str, Any]]:
    """检索与 query 最相关的 top_k 条知识。

    返回 [{id, category, question, answer, score}, ...]
    """
    vectorizer, matrix, docs = _build_index()
    if not docs:
        return []
    qvec = vectorizer.transform([query])
    sims = cosine_similarity(qvec, matrix)[0]
    idx_sorted = np.argsort(sims)[::-1][:top_k]
    out = []
    for i in idx_sorted:
        score = float(sims[i])
        if score < min_score:
            continue
        d = docs[i]
        out.append({
            "id": d["id"],
            "category": d["category"],
            "question": d["question"],
            "answer": d["answer"],
            "score": round(score, 3),
        })
    return out


def format_context(query: str, top_k: int = 3) -> tuple[str, list[dict[str, Any]]]:
    """检索并格式化为可拼进 LLM prompt 的上下文文本。

    返回 (context_text, sources) —— sources 用于前端展示引用来源。
    """
    hits = retrieve(query, top_k=top_k)
    if not hits:
        return "", []
    lines = []
    for h in hits:
        lines.append(
            f"[{h['id']}] 类别:{h['category']}\nQ: {h['question']}\nA: {h['answer']}"
        )
    return "\n\n".join(lines), hits


def reload_kb() -> int:
    """清缓存，下次检索重建索引。管理端点调用。"""
    _load_kb.cache_clear()
    _build_index.cache_clear()
    return len(_load_kb())
