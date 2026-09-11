"""初始化数据库表结构。

用法：
    python scripts/init_db.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import init_db


def main() -> None:
    init_db()
    print("[OK] 数据库表已创建。")


if __name__ == "__main__":
    main()
