"""把 kb_data.json 导入 KBEntry 表。

用法：
    python scripts/seed_kb.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import KBEntry
from app.db.session import SessionLocal, init_db
from app.integrations.llm.knowledge_base import KB_PATH


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(KBEntry).count() > 0:
            print("[SKIP] 知识库表已有数据，跳过导入。")
            return
        with open(KB_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for d in data:
            db.add(KBEntry(
                kb_id=d["id"],
                category=d["category"],
                question=d["question"],
                answer=d["answer"],
                enabled=True,
            ))
        db.commit()
        print(f"[OK] 知识库导入完成：{len(data)} 条 FAQ")
    except Exception as e:
        db.rollback()
        print(f"[ERR] {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
