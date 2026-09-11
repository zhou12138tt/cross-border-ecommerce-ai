"""模拟数据生成器：基于 Faker + numpy 随机生成业务数据。

公开数据集建议（生产阶段可接入）：
    - Amazon Reviews / Amazon Product Data (https://nijianmo.github.io/amazon/)
    - Kaggle E-Commerce Dataset
    - Google Shopping Insights
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import numpy as np
from faker import Faker

fake = Faker()
Faker.seed(42)
random.seed(42)
np.random.seed(42)

CATEGORIES = [
    "智能手表", "无线耳机", "瑜伽垫", "宠物玩具",
    "户外露营灯", "厨房收纳", "美妆刷具", "车载支架",
    "儿童绘本", "电竞键鼠",
]
REGIONS = ["US", "JP", "DE", "GB", "AU", "AE"]
LANG_MAP = {"US": "en", "JP": "ja", "DE": "de", "GB": "en", "AU": "en", "AE": "ar"}
CARRIERS = ["DHL", "FedEx", "UPS", "SF", "YunExpress"]


def gen_products(n: int = 50):
    out = []
    for i in range(n):
        cat = random.choice(CATEGORIES)
        out.append({
            "sku": f"SKU-{i+1:04d}",
            "name": f"{cat}-{fake.word().upper()}",
            "category": cat,
            "region": random.choice(REGIONS),
            "price_usd": round(random.uniform(5, 200), 2),
            "sales_30d": int(np.random.poisson(150)),
            "rating": round(random.uniform(3.8, 5.0), 2),
            "trend_score": round(random.uniform(20, 95), 1),
        })
    return out


def gen_customers(n: int = 30):
    out = []
    for i in range(n):
        region = random.choice(REGIONS)
        out.append({
            "name": fake.name(),
            "country": region,
            "language": LANG_MAP[region],
            "email": fake.email(),
        })
    return out


def gen_warehouses():
    return [
        {"code": f"WH-{r}", "country": r, "capacity_m3": round(random.uniform(1000, 5000), 1)}
        for r in REGIONS
    ]


def gen_inventories(product_ids, warehouse_ids):
    out = []
    for pid in product_ids:
        for wid in warehouse_ids:
            if random.random() < 0.3:  # 30% 商品在 30% 仓库
                continue
            out.append({
                "product_id": pid,
                "warehouse_id": wid,
                "stock": int(np.random.poisson(40)),
                "safety_stock": random.randint(5, 20),
                "in_transit": random.choice([0, 0, 0, 5, 10, 25]),
            })
    return out


def gen_shipments(n: int = 40):
    out = []
    for _ in range(n):
        a, b = random.sample(REGIONS, 2)
        out.append({
            "order_id": f"ORD-{fake.bothify('######')}",
            "origin_country": a,
            "dest_country": b,
            "carrier": random.choice(CARRIERS),
            "status": random.choice(["pending", "shipped", "in_transit", "delivered"]),
            "est_days": random.randint(3, 21),
            "risk_score": round(random.uniform(0, 1), 2),
        })
    return out


def gen_behaviors(customer_ids, product_ids, n: int = 800):
    actions = [("view", 1.0), ("favorite", 5.0), ("purchase", 10.0)]
    out = []
    for _ in range(n):
        a, w = random.choice(actions)
        out.append({
            "customer_id": random.choice(customer_ids),
            "product_id": random.choice(product_ids),
            "action": a,
            "weight": w,
        })
    return out
