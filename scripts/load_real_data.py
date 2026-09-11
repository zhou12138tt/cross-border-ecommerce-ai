"""从公开数据集加载真实跨境商品数据 → DB。

数据源（位于 .cache/datasets/）：
    - amazon-products.csv   (~150 条，53 列)
    - walmart-products.csv  (~42 条)
    - shopee-products.csv   (~5400 条，多品类)
    - shein-products.csv    (~162 条)
    - lazada-products.csv   (~35 条)

字段映射到 Product 表：
    sku           ← asin / id / sku
    name          ← title
    category      ← categories / breadcrumb / root_bs_category
    region        ← domain (com / com.my / ph / vn / sg / co.id / br)
    price_usd     ← final_price (按 currency 粗略换算到 USD)
    sales_30d     ← sold / bought_past_month / number_sold
    rating        ← rating
    trend_score   ← 由 reviews_count / sold 标准化得到的合成趋势分

同步：Competitor（多 seller 的 number_of_sellers + buybox_prices）
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session

from app.db.models import Competitor, Customer, Inventory, Product, Shipment, UserBehavior, Warehouse
from app.db.session import SessionLocal, init_db

DATA_DIR = Path(__file__).resolve().parent.parent / ".cache" / "datasets"

# 简易汇率：→ USD（粗略，仅用于 demo）
CURRENCY_TO_USD = {
    "USD": 1.0, "$": 1.0, "US$": 1.0,
    "MYR": 0.21, "RM": 0.21,
    "SGD": 0.74, "S$": 0.74,
    "PHP": 0.018, "₱": 0.018,
    "VND": 0.000041, "₫": 0.000041,
    "IDR": 0.000065, "Rp": 0.000065,
    "THB": 0.028, "฿": 0.028,
    "BRL": 0.20, "R$": 0.20,
    "TWD": 0.031,
    "EUR": 1.08, "€": 1.08,
    "GBP": 1.27, "£": 1.27,
    "JPY": 0.0067, "¥": 0.0067,
    "CNY": 0.14, "￥": 0.14,
}

# 域名 → 区域代码
DOMAIN_TO_REGION = {
    "www.amazon.com": "US", "amazon.com": "US",
    "www.walmart.com": "US", "walmart.com": "US",
    "shopee.com.my": "MY", "shopee.ph": "PH", "shopee.vn": "VN",
    "shopee.sg": "SG", "shopee.co.id": "ID", "shopee.co.th": "TH",
    "shopee.com.br": "BR", "shopee.tw": "TW",
    "www.lazada.com.my": "MY", "www.lazada.com.ph": "PH",
    "www.lazada.vn": "VN", "www.lazada.sg": "SG",
    "www.lazada.co.id": "ID", "www.lazada.co.th": "TH",
    "www.shein.com": "US", "shein.com": "US",
}


def to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "").replace("₱", "").replace("RM", "")
    s = re.sub(r"[^0-9.\-]", "", s)
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def to_int(v: Any) -> int:
    f = to_float(v)
    if f is None:
        return 0
    return int(f)


def parse_categories(raw: Any) -> str:
    """从 categories / breadcrumb / root_bs_category 字段提取主类目。"""
    if not raw:
        return "其他"
    s = str(raw).strip()
    if not s or s == "[]":
        return "其他"
    # 尝试 JSON 解析
    try:
        if s.startswith("["):
            arr = json.loads(s)
            if isinstance(arr, list) and arr:
                # 取第一个有 name 的
                for it in arr:
                    if isinstance(it, dict) and it.get("name"):
                        return str(it["name"])[:64]
                    if isinstance(it, str):
                        return it[:64]
        if s.startswith("{"):
            obj = json.loads(s)
            if isinstance(obj, dict):
                # breadcrumb 字段
                for key in ("name", "category_name", "root_category_name"):
                    if obj.get(key):
                        return str(obj[key])[:64]
    except (json.JSONDecodeError, ValueError):
        pass
    # 用斜杠/分号分隔
    for sep in ["|", ">", "/"]:
        if sep in s:
            parts = [p.strip() for p in s.split(sep) if p.strip()]
            if parts:
                return parts[0][:64]
    return s[:64]


def parse_region(domain: Any) -> str:
    if not domain:
        return "US"
    d = str(domain).strip().lower()
    return DOMAIN_TO_REGION.get(d, "US")


def parse_currency_to_usd(price: Any, currency: Any) -> Optional[float]:
    p = to_float(price)
    if p is None:
        return None
    cur = (str(currency) if currency else "USD").strip()
    rate = CURRENCY_TO_USD.get(cur, 1.0)
    return round(p * rate, 2)


def parse_sold(raw: Any, alt: Any = None) -> int:
    """解析销量字段，常见格式: 1K+, 100+, 500 sold, 1.2K bought past month。"""
    for v in (raw, alt):
        if not v:
            continue
        s = str(v).strip().lower().replace(",", "")
        if not s or s in ("-", "none"):
            continue
        m = re.match(r"([0-9.]+)\s*([km]?)", s)
        if m:
            num = float(m.group(1))
            unit = m.group(2)
            if unit == "k":
                num *= 1000
            elif unit == "m":
                num *= 1000000
            return int(num)
    return 0


def normalize_rating(r: Any) -> float:
    f = to_float(r)
    if f is None:
        return 4.0
    if f > 5:
        # 可能是百分制
        f = f / 20 if f <= 100 else 4.0
    return round(max(0.0, min(5.0, f)), 1)


def load_amazon(path: Path) -> list[dict]:
    """Amazon 数据：53 列，重点字段 title/final_price/categories/rating/sold/bought_past_month。"""
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = (row.get("asin") or "").strip()
            title = (row.get("title") or "").strip()
            if not sku or not title:
                continue
            price = parse_currency_to_usd(row.get("final_price"), row.get("currency"))
            if not price or price < 0.5:
                price = parse_currency_to_usd(row.get("initial_price"), row.get("currency")) or 9.9
            sold = parse_sold(row.get("bought_past_month"), row.get("root_bs_rank"))
            reviews = to_int(row.get("reviews_count"))
            # 趋势分：reviews 数 + 销量 综合标准化
            trend = min(100.0, (sold / 50.0) + (reviews / 50.0))
            out.append({
                "sku": f"AMZ-{sku}",
                "name": title[:255],
                "category": parse_categories(row.get("categories") or row.get("root_bs_category")),
                "region": parse_region(row.get("domain")),
                "price_usd": price,
                "sales_30d": min(sold, 10000),
                "rating": normalize_rating(row.get("rating")),
                "trend_score": round(trend, 1),
                "source": "amazon",
                "competitors_data": _parse_amazon_competitors(row),
            })
    return out


def _parse_amazon_competitors(row: dict) -> list[dict]:
    """Amazon 多卖家场景：buybox_prices 是 JSON 价格数组。"""
    raw = row.get("buybox_prices")
    if not raw:
        return []
    try:
        arr = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(arr, list):
            return []
        out = []
        for p in arr[:3]:
            price = to_float(p)
            if price:
                out.append({
                    "competitor_name": "Amazon 其他卖家",
                    "competitor_price": price,
                    "market_share": 0.3,
                })
        return out
    except (json.JSONDecodeError, TypeError):
        return []


def load_walmart(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = (row.get("product_id") or row.get("sku") or "").strip()
            title = (row.get("product_name") or "").strip()
            if not sku or not title:
                continue
            price = parse_currency_to_usd(row.get("final_price"), row.get("currency"))
            if not price or price < 0.5:
                price = parse_currency_to_usd(row.get("initial_price"), row.get("currency")) or 12.9
            sold = to_int(row.get("review_count"))
            out.append({
                "sku": f"WMT-{sku[:20]}",
                "name": title[:255],
                "category": parse_categories(row.get("category_name") or row.get("breadcrumbs")),
                "region": "US",
                "price_usd": price,
                "sales_30d": min(sold, 5000),
                "rating": normalize_rating(row.get("rating")),
                "trend_score": round(min(80.0, sold / 30.0), 1),
                "source": "walmart",
                "competitors_data": [],
            })
    return out


def load_shopee(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sku = (row.get("id") or "").strip()
            title = (row.get("title") or "").strip()
            if not sku or not title:
                continue
            price = parse_currency_to_usd(row.get("final_price"), row.get("currency"))
            if not price or price < 0.5:
                price = parse_currency_to_usd(row.get("initial_price"), row.get("currency")) or 5.9
            sold = parse_sold(row.get("sold"))
            reviews = to_int(row.get("reviews"))
            out.append({
                "sku": f"SHP-{sku[:20]}",
                "name": title[:255],
                "category": parse_categories(row.get("breadcrumb")),
                "region": parse_region(row.get("domain")),
                "price_usd": price,
                "sales_30d": min(sold, 10000),
                "rating": normalize_rating(row.get("rating")),
                "trend_score": round(min(100.0, (sold / 20.0) + (reviews / 30.0)), 1),
                "source": "shopee",
                "competitors_data": _parse_shopee_seller(row),
            })
    return out


def _parse_shopee_seller(row: dict) -> list[dict]:
    """Shopee 同店一个卖家，但可生成同品类竞品占位。"""
    seller = (row.get("seller_name") or "").strip()
    rating = to_float(row.get("seller_rating"))
    if not seller:
        return []
    return [{
        "competitor_name": f"Shopee - {seller[:30]}",
        "competitor_price": to_float(row.get("final_price")) or 9.9,
        "market_share": round(rating / 100.0, 2) if rating else 0.5,
    }]


def load_shein(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("title") or row.get("product_name") or "").strip()
            if not title:
                continue
            sku = (row.get("goods_id") or row.get("sku") or f"SHEIN-{hash(title) & 0xFFFF}").strip()
            price = parse_currency_to_usd(row.get("final_price"), row.get("currency"))
            if not price or price < 0.5:
                price = 19.9
            sold = parse_sold(row.get("sold"), row.get("reviews_count"))
            out.append({
                "sku": f"SHN-{str(sku)[:20]}",
                "name": title[:255],
                "category": parse_categories(row.get("categories") or row.get("breadcrumb")),
                "region": "US",
                "price_usd": price,
                "sales_30d": min(sold, 5000),
                "rating": normalize_rating(row.get("rating")),
                "trend_score": round(min(70.0, sold / 40.0), 1),
                "source": "shein",
                "competitors_data": [],
            })
    return out


def load_lazada(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            sku = (row.get("sku") or row.get("mpn") or f"LZD-{hash(title) & 0xFFFF}").strip()
            price = parse_currency_to_usd(row.get("final_price"), row.get("currency"))
            if not price or price < 0.5:
                price = parse_currency_to_usd(row.get("initial_price"), row.get("currency")) or 15.9
            sold = parse_sold(row.get("number_sold"), row.get("gmv"))
            out.append({
                "sku": f"LZD-{str(sku)[:20]}",
                "name": title[:255],
                "category": parse_categories(row.get("breadcrumb")),
                "region": parse_region(row.get("domain")),
                "price_usd": price,
                "sales_30d": min(sold, 5000),
                "rating": normalize_rating(row.get("rating")),
                "trend_score": round(min(60.0, sold / 30.0), 1),
                "source": "lazada",
                "competitors_data": _parse_lazada_seller(row),
            })
    return out


def _parse_lazada_seller(row: dict) -> list[dict]:
    seller = (row.get("seller_name") or "").strip()
    if not seller:
        return []
    ship_rate = to_float(row.get("seller_ship_on_time"))
    return [{
        "competitor_name": f"Lazada - {seller[:30]}",
        "competitor_price": to_float(row.get("final_price")) or 19.9,
        "market_share": round(ship_rate / 100.0, 2) if ship_rate else 0.5,
    }]


# ---------- 派生客户/行为/物流 ----------
COUNTRIES_BY_REGION = {
    "US": ["US", "CA", "MX"], "MY": ["MY", "SG"], "PH": ["PH"], "VN": ["VN"],
    "SG": ["SG", "MY"], "ID": ["ID"], "TH": ["TH"], "BR": ["BR"], "TW": ["TW"],
}
LANGS_BY_COUNTRY = {
    "US": "en", "CA": "en", "MX": "es", "MY": "en", "SG": "en", "PH": "en",
    "VN": "vi", "ID": "id", "TH": "th", "BR": "pt", "TW": "zh",
}
NAMES_POOL = [
    "Emma", "Liam", "Olivia", "Noah", "Ava", "Ethan", "Sophia", "Mason", "Isabella", "Lucas",
    "Mia", "Hiroshi", "Yuki", "Tanaka", "Wei", "Min", "Jin", "Sung-Hee", "Min-Jun", "Fatima",
    "Ali", "Hassan", "Priya", "Arjun", "Raj", "Anya", "Sofia", "Mateo", "Diego", "Camila",
    "Lucas", "Joaquin", "Valentina", "Sara", "Mohamed", "Chen", "Mei", "Jin-Ho", "Park",
]


def derive_customers_and_behaviors(products: list[dict], n_customers: int = 200) -> tuple[list[dict], list[dict]]:
    """基于真实商品派生客户和用户行为日志（模拟购买）。"""
    import random
    random.seed(42)

    customers = []
    for i in range(n_customers):
        country = random.choice(["US", "GB", "DE", "JP", "FR", "BR", "SG", "AU", "AE"])
        lang = {"US": "en", "GB": "en", "DE": "de", "JP": "ja", "FR": "en",
                "BR": "pt", "SG": "en", "AU": "en", "AE": "ar"}.get(country, "en")
        customers.append({
            "id_counter": i + 1,
            "name": random.choice(NAMES_POOL) + " " + random.choice(NAMES_POOL),
            "country": country,
            "language": lang,
            "email": f"customer{i+1}@example.com",
        })

    behaviors = []
    # 让每个商品都被 1-15 个客户浏览/购买
    for prod in products:
        pid = prod["id"]
        n_interactions = random.randint(1, 15)
        for _ in range(n_interactions):
            cust = random.choice(customers)
            action = random.choices(["view", "favorite", "purchase"], weights=[0.7, 0.15, 0.15])[0]
            weight = {"view": 1.0, "favorite": 5.0, "purchase": 10.0}[action]
            behaviors.append({
                "customer_idx": cust["id_counter"],
                "product_id": pid,
                "action": action,
                "weight": weight,
            })
    return customers, behaviors


def derive_shipments(products: list[dict], customers: list[dict], n: int = 200) -> list[dict]:
    """派生物流运单：客户从商品原产地国家购买，运往客户国家。"""
    import random
    random.seed(7)

    CARRIERS = ["DHL", "FedEx", "UPS", "YunExpress", "4PX", "EMS"]
    STATUSES = ["delivered", "in_transit", "customs", "delayed", "pending"]
    ship_out = []
    for _ in range(n):
        prod = random.choice(products)
        cust = random.choice(customers)
        origin = prod["region"]
        dest = cust["country"]
        if origin == dest:
            dest = random.choice(["US", "GB", "DE", "JP"])
        carrier = random.choice(CARRIERS)
        # 估算时效：跨区域 7-21 天
        base_days = 7 if origin == dest else 14
        est_days = base_days + random.randint(-3, 7)
        status = random.choices(STATUSES, weights=[0.5, 0.2, 0.1, 0.1, 0.1])[0]
        risk = round(random.uniform(0.05, 0.95), 2)
        if status == "delayed":
            risk = round(max(risk, 0.7), 2)
        ship_out.append({
            "order_id": f"ORD-{random.randint(10000, 99999)}",
            "origin_country": origin,
            "dest_country": dest,
            "carrier": carrier,
            "status": status,
            "est_days": max(3, est_days),
            "risk_score": risk,
        })
    return ship_out


def derive_inventory(products: list[dict], warehouses: list[dict]) -> list[dict]:
    """每个商品在 1-2 个仓库有库存。"""
    import random
    random.seed(99)
    inv = []
    for prod in products:
        n_wh = random.randint(1, min(2, len(warehouses)))
        for wh in random.sample(warehouses, n_wh):
            stock = random.randint(0, 200)
            safety = random.randint(20, 80)
            inv.append({
                "product_id": prod["id"],
                "warehouse_id": wh["id"],
                "stock": stock,
                "safety_stock": safety,
                "in_transit": random.randint(0, 100),
            })
    return inv


# ---------- 主入口 ----------
def main() -> None:
    print("=" * 60)
    print("从公开数据集加载真实跨境商品数据")
    print("=" * 60)

    # 1. 加载所有 CSV
    all_products_data: list[dict] = []
    loaders = [
        ("Amazon", "amazon-products.csv", load_amazon),
        ("Walmart", "walmart-products.csv", load_walmart),
        ("Shopee", "shopee-products.csv", load_shopee),
        ("Shein", "shein-products.csv", load_shein),
        ("Lazada", "lazada-products.csv", load_lazada),
    ]
    for label, fname, fn in loaders:
        path = DATA_DIR / fname
        if not path.exists():
            print(f"[SKIP] {label}: 文件不存在 {path}")
            continue
        items = fn(path)
        print(f"[OK] {label}: {len(items)} 条商品")
        all_products_data.extend(items)

    # 去重（按 sku）
    seen_skus = set()
    unique_products = []
    for p in all_products_data:
        if p["sku"] in seen_skus:
            continue
        seen_skus.add(p["sku"])
        unique_products.append(p)
    print(f"[INFO] 去重后商品数：{len(unique_products)}")

    if not unique_products:
        print("[ERR] 没有可用商品，终止。")
        return

    # 2. 初始化 DB
    init_db()
    db = SessionLocal()
    try:
        # 清空旧数据（保留 KB）
        for model in [Competitor, UserBehavior, Shipment, Inventory, Customer, Warehouse, Product]:
            db.query(model).delete()
            db.commit()
        print("[OK] 旧业务数据已清空（KB 保留）")

        # 3. 商品 + 竞品
        now = datetime.utcnow()
        for i, p in enumerate(unique_products):
            prod = Product(
                sku=p["sku"], name=p["name"], category=p["category"],
                region=p["region"], price_usd=p["price_usd"],
                sales_30d=p["sales_30d"], rating=p["rating"],
                trend_score=p["trend_score"],
                created_at=now, updated_at=now,
            )
            db.add(prod)
            db.flush()  # 拿 id
            p["id"] = prod.id
            # 竞品
            for c in p.get("competitors_data", []):
                db.add(Competitor(
                    product_id=prod.id,
                    competitor_name=c["competitor_name"],
                    competitor_price=c["competitor_price"],
                    market_share=c["market_share"],
                    snapshot_date=now,
                    created_at=now, updated_at=now,
                ))
        db.commit()
        print(f"[OK] 写入商品 {len(unique_products)} 条 + 竞品")

        # 4. 仓库（按区域）
        regions_seen = sorted({p["region"] for p in unique_products})
        warehouses_data = []
        for region in regions_seen[:8]:
            wh = Warehouse(
                code=f"WH-{region}", country=region,
                capacity_m3=5000.0,
                created_at=now, updated_at=now,
            )
            db.add(wh)
            db.flush()
            warehouses_data.append({"id": wh.id, "region": region})
        # 补一个全球仓
        global_wh = Warehouse(
            code="WH-GLOBAL", country="US", capacity_m3=20000.0,
            created_at=now, updated_at=now,
        )
        db.add(global_wh)
        db.flush()
        warehouses_data.append({"id": global_wh.id, "region": "GLOBAL"})
        db.commit()
        print(f"[OK] 仓库 {len(warehouses_data)} 个")

        # 5. 客户 + 行为
        customers_data, behaviors_data = derive_customers_and_behaviors(unique_products, n_customers=200)
        for c in customers_data:
            db.add(Customer(
                name=c["name"], country=c["country"], language=c["language"],
                email=c["email"], created_at=now, updated_at=now,
            ))
        db.commit()
        # 拿所有客户 id
        cust_rows = db.query(Customer).order_by(Customer.id).all()
        cust_id_by_idx = {i + 1: c.id for i, c in enumerate(cust_rows)}
        for b in behaviors_data:
            db.add(UserBehavior(
                customer_id=cust_id_by_idx[b["customer_idx"]],
                product_id=b["product_id"],
                action=b["action"], weight=b["weight"],
                created_at=now, updated_at=now,
            ))
        db.commit()
        print(f"[OK] 客户 {len(customers_data)} + 行为日志 {len(behaviors_data)} 条")

        # 6. 库存
        inv_data = derive_inventory(unique_products, warehouses_data)
        for inv in inv_data:
            db.add(Inventory(
                product_id=inv["product_id"], warehouse_id=inv["warehouse_id"],
                stock=inv["stock"], safety_stock=inv["safety_stock"],
                in_transit=inv["in_transit"],
                created_at=now, updated_at=now,
            ))
        db.commit()
        print(f"[OK] 库存记录 {len(inv_data)} 条")

        # 7. 物流运单
        ship_data = derive_shipments(unique_products, customers_data, n=300)
        for s in ship_data:
            db.add(Shipment(
                order_id=s["order_id"], origin_country=s["origin_country"],
                dest_country=s["dest_country"], carrier=s["carrier"],
                status=s["status"], est_days=s["est_days"],
                risk_score=s["risk_score"],
                created_at=now, updated_at=now,
            ))
        db.commit()
        print(f"[OK] 物流运单 {len(ship_data)} 条")

        # 8. 摘要
        print("\n" + "=" * 60)
        print("加载完成摘要：")
        print(f"  商品: {db.query(Product).count()} 条 (覆盖 {len(regions_seen)} 区域)")
        print(f"  竞品: {db.query(Competitor).count()} 条")
        print(f"  客户: {db.query(Customer).count()} 个")
        print(f"  行为: {db.query(UserBehavior).count()} 条")
        print(f"  仓库: {db.query(Warehouse).count()} 个")
        print(f"  库存: {db.query(Inventory).count()} 条")
        print(f"  运单: {db.query(Shipment).count()} 条")
        print(f"  知识库: {db.query(Competitor).count() and '保留中'} 条（未清空）")

        # 平台分布
        from sqlalchemy import func as sqlfn
        platform_stats = db.query(Product.region, sqlfn.count(Product.id)).group_by(Product.region).all()
        print("\n  按区域分布:")
        for region, cnt in platform_stats:
            print(f"    {region}: {cnt} 个商品")

        top_cats = db.query(Product.category, sqlfn.count(Product.id), sqlfn.sum(Product.sales_30d)) \
            .group_by(Product.category).order_by(sqlfn.sum(Product.sales_30d).desc()).limit(5).all()
        print("\n  TOP 5 类目（按30天销量）:")
        for cat, cnt, sales in top_cats:
            print(f"    {cat}: {cnt} 个商品, 总销量 {int(sales or 0)}")
        print("=" * 60)
    finally:
        db.close()


if __name__ == "__main__":
    main()
