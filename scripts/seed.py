"""灌入模拟数据。

用法：
    python scripts/seed.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.models import (
    Competitor,
    Customer,
    Inventory,
    Product,
    Shipment,
    UserBehavior,
    Warehouse,
)
from app.db.session import SessionLocal, init_db
from app.utils.mock_data import (
    fake,
    gen_behaviors,
    gen_customers,
    gen_inventories,
    gen_products,
    gen_shipments,
    gen_warehouses,
)


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(Product).count() > 0:
            print("[SKIP] 已存在数据，跳过种子。")
            return

        # 商品 + 竞品
        products = [Product(**p) for p in gen_products()]
        db.add_all(products)
        db.flush()
        for p in products:
            for _ in range(2):
                db.add(Competitor(
                    product_id=p.id,
                    competitor_name=fake_cn_comp(),
                    competitor_price=round(p.price_usd * random.uniform(0.8, 1.2), 2),
                    market_share=round(random.uniform(0.05, 0.4), 2),
                ))

        # 客户
        customers = [Customer(**c) for c in gen_customers()]
        db.add_all(customers)
        db.flush()

        # 仓库 + 库存
        whs = [Warehouse(**w) for w in gen_warehouses()]
        db.add_all(whs)
        db.flush()
        db.add_all([Inventory(**i) for i in gen_inventories(
            [p.id for p in products], [w.id for w in whs]
        )])

        # 物流
        db.add_all([Shipment(**s) for s in gen_shipments()])

        # 行为日志
        db.add_all([UserBehavior(**b) for b in gen_behaviors(
            [c.id for c in customers], [p.id for p in products]
        )])

        db.commit()
        print(f"[OK] 灌入完成：商品 {len(products)} / 客户 {len(customers)} / 仓库 {len(whs)}")
    except Exception as e:
        db.rollback()
        print(f"[ERR] {e}")
        raise
    finally:
        db.close()


def fake_cn_comp() -> str:
    return f"{fake.company()} {fake.suffix()}"


if __name__ == "__main__":
    import random  # noqa: F401
    main()
