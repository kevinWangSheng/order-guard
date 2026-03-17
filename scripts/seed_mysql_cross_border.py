#!/usr/bin/env python3
"""
Seed script for cross-border e-commerce tables in OrderGuard ERP MySQL database.

Adds 6 new tables: shipping_methods, shipping_costs, listings,
listing_performance, replenishment_plans, fba_inventory.

Usage:
    python scripts/seed_mysql_cross_border.py

Connects via subprocess: mysql -u root orderguard_erp
"""

from __future__ import annotations

import random
import subprocess
import sys
import datetime
from decimal import Decimal, ROUND_HALF_UP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
TODAY = datetime.date(2026, 3, 8)
random.seed(42)

MYSQL_CMD = ["mysql", "-u", "root", "orderguard_erp"]


def run_sql(sql: str) -> str:
    """Pipe SQL into mysql via subprocess."""
    result = subprocess.run(
        MYSQL_CMD,
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"MySQL ERROR:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
-- -------------------------------------------------------
-- 1. shipping_methods — 物流方式
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS shipping_methods (
  id INT AUTO_INCREMENT PRIMARY KEY,
  name VARCHAR(100) NOT NULL COMMENT '物流方式名称',
  type ENUM('sea','air','express','rail') NOT NULL COMMENT '运输类型',
  origin VARCHAR(50) DEFAULT '深圳' COMMENT '发货地',
  destination VARCHAR(50) COMMENT '目的地',
  transit_days_min INT COMMENT '最短运输天数',
  transit_days_max INT COMMENT '最长运输天数',
  cost_per_kg DECIMAL(10,2) COMMENT '每公斤成本(USD)',
  cost_per_cbm DECIMAL(10,2) COMMENT '每立方米成本(USD)',
  min_weight_kg DECIMAL(10,2) DEFAULT 0 COMMENT '最低起运重量',
  status ENUM('active','suspended') DEFAULT 'active'
) COMMENT='物流方式配置';

-- -------------------------------------------------------
-- 2. shipping_costs — 实际物流费用记录
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS shipping_costs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  shipment_id VARCHAR(50) NOT NULL COMMENT '运单号',
  shipping_method_id INT COMMENT 'FK shipping_methods',
  sku VARCHAR(50) COMMENT '关联SKU',
  quantity INT COMMENT '数量',
  weight_kg DECIMAL(10,2) COMMENT '实际重量',
  volume_cbm DECIMAL(10,4) COMMENT '实际体积',
  shipping_cost_usd DECIMAL(10,2) COMMENT '运费(USD)',
  customs_duty_usd DECIMAL(10,2) DEFAULT 0 COMMENT '关税(USD)',
  insurance_usd DECIMAL(10,2) DEFAULT 0 COMMENT '保险(USD)',
  total_logistics_cost_usd DECIMAL(10,2) COMMENT '物流总成本',
  ship_date DATE,
  estimated_arrival DATE,
  actual_arrival DATE,
  status ENUM('pending','in_transit','customs','delivered','delayed') DEFAULT 'pending',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
) COMMENT='实际物流费用记录';

-- -------------------------------------------------------
-- 3. listings — Listing 信息
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS listings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sku VARCHAR(50) NOT NULL,
  platform ENUM('amazon_us','amazon_eu','amazon_jp','shopee','lazada') NOT NULL,
  asin VARCHAR(20) COMMENT 'Amazon ASIN',
  listing_title VARCHAR(500),
  listing_url VARCHAR(500),
  price_usd DECIMAL(10,2),
  status ENUM('active','inactive','suppressed','out_of_stock') DEFAULT 'active',
  created_at DATE,
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
) COMMENT='各平台 Listing 信息';

-- -------------------------------------------------------
-- 4. listing_performance — Listing 每日表现数据
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS listing_performance (
  id INT AUTO_INCREMENT PRIMARY KEY,
  listing_id INT COMMENT 'FK listings',
  sku VARCHAR(50),
  platform VARCHAR(20),
  snapshot_date DATE,
  sessions INT COMMENT '访问量',
  page_views INT COMMENT '浏览量',
  units_ordered INT COMMENT '订购数',
  conversion_rate DECIMAL(5,2) COMMENT '转化率%%',
  buy_box_pct DECIMAL(5,2) COMMENT 'Buy Box 占比%%',
  bsr_rank INT COMMENT 'Best Seller Rank',
  category_rank INT COMMENT '类目排名',
  review_count INT COMMENT '累计评论数',
  review_rating DECIMAL(3,2) COMMENT '平均评分',
  new_reviews_today INT DEFAULT 0 COMMENT '当日新增评论',
  ad_spend_usd DECIMAL(10,2) DEFAULT 0 COMMENT '广告花费',
  ad_sales_usd DECIMAL(10,2) DEFAULT 0 COMMENT '广告销售额',
  acos DECIMAL(5,2) COMMENT 'ACoS%%',
  INDEX idx_sku_date (sku, snapshot_date),
  INDEX idx_listing_date (listing_id, snapshot_date)
) COMMENT='Listing 每日表现数据';

-- -------------------------------------------------------
-- 5. replenishment_plans — 补货计划
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS replenishment_plans (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sku VARCHAR(50) NOT NULL,
  warehouse_id INT COMMENT 'FK warehouses (destination)',
  plan_type ENUM('regular','urgent','seasonal') DEFAULT 'regular',
  recommended_qty INT COMMENT '建议补货数量',
  approved_qty INT COMMENT '审批数量',
  shipping_method_id INT COMMENT 'FK shipping_methods',
  estimated_cost_usd DECIMAL(10,2),
  status ENUM('draft','approved','in_procurement','shipped','completed','cancelled') DEFAULT 'draft',
  target_arrival_date DATE COMMENT '期望到货日期',
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  approved_at DATETIME,
  notes TEXT
) COMMENT='补货计划';

-- -------------------------------------------------------
-- 6. fba_inventory — FBA 库存
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS fba_inventory (
  id INT AUTO_INCREMENT PRIMARY KEY,
  sku VARCHAR(50) NOT NULL,
  marketplace VARCHAR(20) NOT NULL COMMENT 'amazon_us/amazon_eu/amazon_jp',
  fulfillable_qty INT DEFAULT 0 COMMENT '可售库存',
  inbound_qty INT DEFAULT 0 COMMENT '入库中',
  reserved_qty INT DEFAULT 0 COMMENT '预留',
  unfulfillable_qty INT DEFAULT 0 COMMENT '不可售(损坏等)',
  days_of_supply INT COMMENT '预计可售天数',
  restock_needed BOOLEAN DEFAULT FALSE COMMENT '是否需要补货',
  last_updated DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_sku (sku)
) COMMENT='亚马逊 FBA 库存';
"""


# ---------------------------------------------------------------------------
# Data generation helpers
# ---------------------------------------------------------------------------
def esc(v) -> str:
    """Escape a value for SQL."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return f"'{v}'"
    s = str(v).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{s}'"


def insert_rows(table: str, columns: list[str], rows: list[tuple]) -> str:
    """Generate INSERT statements in batches of 100."""
    stmts = []
    cols = ", ".join(columns)
    for i in range(0, len(rows), 100):
        batch = rows[i : i + 100]
        values = ",\n".join(
            "(" + ", ".join(esc(v) for v in row) + ")" for row in batch
        )
        stmts.append(f"INSERT INTO {table} ({cols}) VALUES\n{values};")
    return "\n".join(stmts)


def rand_date(start: datetime.date, end: datetime.date) -> datetime.date:
    delta = (end - start).days
    return start + datetime.timedelta(days=random.randint(0, max(0, delta)))


# ---------------------------------------------------------------------------
# 1. shipping_methods seed data
# ---------------------------------------------------------------------------
def gen_shipping_methods() -> str:
    columns = [
        "name", "type", "origin", "destination",
        "transit_days_min", "transit_days_max",
        "cost_per_kg", "cost_per_cbm", "min_weight_kg", "status",
    ]
    rows = [
        ("海运普船(美西)", "sea", "深圳", "洛杉矶", 30, 40, 3.5, 150.00, 100, "active"),
        ("海运快船(美西)", "sea", "深圳", "洛杉矶", 18, 25, 5.8, 220.00, 100, "active"),
        ("空运普货(美国)", "air", "深圳", "美国", 7, 12, 28.00, None, 21, "active"),
        ("空运特快(美国)", "air", "深圳", "美国", 3, 5, 45.00, None, 0.5, "active"),
        ("国际快递DHL", "express", "深圳", "全球", 3, 7, 55.00, None, 0.5, "active"),
        ("中欧铁路", "rail", "深圳", "欧洲", 18, 22, 4.2, None, 50, "active"),
        ("海运(欧洲)", "sea", "深圳", "汉堡", 35, 45, 3.8, None, 100, "active"),
        ("海运(日本)", "sea", "深圳", "东京", 7, 12, 2.5, None, 50, "active"),
    ]
    return insert_rows("shipping_methods", columns, rows)


# ---------------------------------------------------------------------------
# 2. shipping_costs seed data (~200 rows)
# ---------------------------------------------------------------------------
def gen_shipping_costs() -> str:
    columns = [
        "shipment_id", "shipping_method_id", "sku", "quantity",
        "weight_kg", "volume_cbm", "shipping_cost_usd",
        "customs_duty_usd", "insurance_usd", "total_logistics_cost_usd",
        "ship_date", "estimated_arrival", "actual_arrival", "status",
    ]

    skus = [f"SKU-ERP-{i:03d}" for i in range(1, 51)]
    # method_id -> (type, base_cost_per_kg, transit_min, transit_max)
    methods = {
        1: ("sea", 3.5, 30, 40),
        2: ("sea", 5.8, 18, 25),
        3: ("air", 28.0, 7, 12),
        4: ("air", 45.0, 3, 5),
        5: ("express", 55.0, 3, 7),
        6: ("rail", 4.2, 18, 22),
        7: ("sea", 3.8, 35, 45),
        8: ("sea", 2.5, 7, 12),
    }

    rows = []
    six_months_ago = TODAY - datetime.timedelta(days=180)
    thirty_days_ago = TODAY - datetime.timedelta(days=30)

    for i in range(1, 201):
        shipment_id = f"SHP-2026-{i:05d}"
        method_id = random.choice(list(methods.keys()))
        mtype, base_cost, tmin, tmax = methods[method_id]
        sku = random.choice(skus)
        quantity = random.randint(50, 2000)
        weight_kg = round(random.uniform(10, 500), 2)
        volume_cbm = round(random.uniform(0.05, 5.0), 4)

        ship_date = rand_date(six_months_ago, TODAY - datetime.timedelta(days=3))
        transit_days = random.randint(tmin, tmax)
        est_arrival = ship_date + datetime.timedelta(days=transit_days)

        # Anomaly: recent air freight cost increased ~35%
        is_recent = ship_date >= thirty_days_ago
        if is_recent and mtype == "air":
            cost_per_kg = round(base_cost * random.uniform(1.30, 1.40), 2)
        else:
            cost_per_kg = round(base_cost * random.uniform(0.90, 1.10), 2)

        shipping_cost = round(float(weight_kg) * cost_per_kg, 2)
        customs_duty = round(shipping_cost * random.uniform(0.03, 0.12), 2)
        insurance = round(shipping_cost * random.uniform(0.005, 0.02), 2)
        total_cost = round(shipping_cost + customs_duty + insurance, 2)

        # Determine status and actual_arrival
        if est_arrival > TODAY:
            status = random.choice(["pending", "in_transit", "customs"])
            actual_arrival = None
        else:
            # Anomaly: some delayed shipments
            if random.random() < 0.12:
                delay = random.randint(7, 25)
                actual_arrival = est_arrival + datetime.timedelta(days=delay)
                status = "delayed"
            else:
                actual_arrival = est_arrival + datetime.timedelta(
                    days=random.randint(-2, 3)
                )
                status = "delivered"

        rows.append((
            shipment_id, method_id, sku, quantity,
            weight_kg, volume_cbm, shipping_cost,
            customs_duty, insurance, total_cost,
            ship_date, est_arrival, actual_arrival, status,
        ))

    return insert_rows("shipping_costs", columns, rows)


# ---------------------------------------------------------------------------
# 3. listings seed data (~30 SKUs × 2-3 platforms)
# ---------------------------------------------------------------------------
PRODUCT_NAMES = [
    "Wireless Bluetooth Earbuds", "LED Desk Lamp Dimmable", "Yoga Mat Non-Slip",
    "Phone Case Silicone Clear", "USB-C Hub 7-in-1", "Smart Watch Band",
    "Portable Blender Mini", "Car Phone Mount Magnetic", "Electric Toothbrush Head",
    "Kitchen Scale Digital", "Laptop Stand Aluminum", "Pet Grooming Brush",
    "Baby Monitor Camera", "Travel Adapter Universal", "Ring Light 10 inch",
    "Resistance Bands Set", "Water Bottle Insulated", "Desk Organizer Bamboo",
    "Solar Power Bank 20000mAh", "Bike Phone Holder",
    "Air Purifier Filter HEPA", "Massage Gun Mini", "Camping Lantern LED",
    "Keyboard Wrist Rest Gel", "Drawing Tablet 10 inch",
    "Coffee Grinder Manual", "Jump Rope Weighted", "Plant Grow Light",
    "Shower Head High Pressure", "Notebook Stand Adjustable",
]

def gen_listings() -> str:
    columns = [
        "sku", "platform", "asin", "listing_title",
        "listing_url", "price_usd", "status", "created_at",
    ]

    platforms_pool = ["amazon_us", "amazon_eu", "amazon_jp", "shopee", "lazada"]
    rows = []

    for i in range(1, 31):
        sku = f"SKU-ERP-{i:03d}"
        name = PRODUCT_NAMES[i - 1]
        # Each SKU on 2-3 platforms
        n_platforms = random.choice([2, 2, 3])
        chosen = random.sample(platforms_pool, n_platforms)
        # Ensure amazon_us is often included
        if "amazon_us" not in chosen:
            chosen[0] = "amazon_us"

        for platform in chosen:
            asin = None
            if platform.startswith("amazon"):
                asin = f"B0{random.randint(10000000, 99999999)}"

            price = round(random.uniform(9.99, 89.99), 2)
            created = rand_date(
                TODAY - datetime.timedelta(days=365),
                TODAY - datetime.timedelta(days=60),
            )

            # Anomaly statuses
            if sku == "SKU-ERP-015" and platform == "amazon_us":
                status = "out_of_stock"
            elif random.random() < 0.06:
                status = "suppressed"
            else:
                status = "active"

            url = f"https://{platform.replace('_', '.')}.example.com/dp/{asin or sku}"
            title = f"{name} - {platform.upper().replace('_', ' ')} Edition"

            rows.append((
                sku, platform, asin, title, url, price, status, created,
            ))

    return insert_rows("listings", columns, rows)


# ---------------------------------------------------------------------------
# 4. listing_performance seed data (30 days × ~60 listings)
# ---------------------------------------------------------------------------
def gen_listing_performance(listing_count: int) -> str:
    columns = [
        "listing_id", "sku", "platform", "snapshot_date",
        "sessions", "page_views", "units_ordered", "conversion_rate",
        "buy_box_pct", "bsr_rank", "category_rank",
        "review_count", "review_rating", "new_reviews_today",
        "ad_spend_usd", "ad_sales_usd", "acos",
    ]

    rows = []
    start_date = TODAY - datetime.timedelta(days=29)

    for listing_id in range(1, listing_count + 1):
        # Derive sku index from listing_id — listings are sequential
        # We'll pass in a mapping instead
        pass

    # We need listing info; regenerate the mapping
    listings_info = []
    platforms_pool = ["amazon_us", "amazon_eu", "amazon_jp", "shopee", "lazada"]
    random.seed(42)  # Reset to match listing generation
    for i in range(1, 31):
        sku = f"SKU-ERP-{i:03d}"
        n_platforms = random.choice([2, 2, 3])
        chosen = random.sample(platforms_pool, n_platforms)
        if "amazon_us" not in chosen:
            chosen[0] = "amazon_us"
        for platform in chosen:
            listings_info.append((sku, platform))

    random.seed(99)  # Different seed for performance data variety

    for lid, (sku, platform) in enumerate(listings_info, start=1):
        # Base metrics
        base_sessions = random.randint(50, 800)
        base_conv = round(random.uniform(3.0, 18.0), 2)
        base_bsr = random.randint(500, 50000)
        base_cat_rank = random.randint(10, 2000)
        base_review_count = random.randint(20, 500)
        base_rating = round(random.uniform(3.8, 4.8), 2)
        base_ad_spend = round(random.uniform(5, 80), 2)

        for day_offset in range(30):
            snap_date = start_date + datetime.timedelta(days=day_offset)
            day_pct = day_offset / 29.0  # 0.0 to 1.0

            sessions = max(10, base_sessions + random.randint(-50, 50))
            page_views = int(sessions * random.uniform(1.2, 1.8))
            conv_rate = max(0.5, base_conv + random.uniform(-2, 2))
            units_ordered = max(0, int(sessions * conv_rate / 100))
            buy_box_pct = round(random.uniform(60, 100), 2)
            bsr = max(1, base_bsr + random.randint(-200, 200))
            cat_rank = max(1, base_cat_rank + random.randint(-50, 50))
            review_count = base_review_count + day_offset  # grows daily
            rating = round(base_rating + random.uniform(-0.1, 0.1), 2)
            new_reviews = random.choice([0, 0, 0, 1, 1, 2])
            ad_spend = round(base_ad_spend * random.uniform(0.7, 1.3), 2)
            ad_sales = round(ad_spend * random.uniform(1.5, 5.0), 2)
            acos = round((ad_spend / ad_sales) * 100, 2) if ad_sales > 0 else 0

            # --- Anomaly: SKU-ERP-031 rating drop ---
            if sku == "SKU-ERP-031":
                # Rating drops from 4.3 to 3.1 over 30 days
                rating = round(4.3 - (1.2 * day_pct), 2)
                new_reviews = random.choice([2, 3, 4, 5])  # lots of bad reviews

            # --- Anomaly: SKU-ERP-042 sessions & conversion spike ---
            if sku == "SKU-ERP-042" and day_offset >= 20:
                sessions = int(base_sessions * random.uniform(5.0, 6.0))
                page_views = int(sessions * 1.5)
                conv_rate = round(random.uniform(25, 35), 2)
                units_ordered = int(sessions * conv_rate / 100)
                buy_box_pct = 99.0

            # --- Anomaly: SKU-ERP-015 BSR improving but out of stock ---
            if sku == "SKU-ERP-015" and platform == "amazon_us":
                bsr = max(1, int(5000 - 150 * day_offset))  # improving
                if day_offset >= 20:
                    units_ordered = 0
                    sessions = max(5, int(sessions * 0.3))

            # --- Anomaly: some listings with ACoS > 50% ---
            if sku in ("SKU-ERP-008", "SKU-ERP-019", "SKU-ERP-027"):
                ad_spend = round(random.uniform(60, 120), 2)
                ad_sales = round(ad_spend * random.uniform(0.8, 1.5), 2)
                acos = round((ad_spend / ad_sales) * 100, 2) if ad_sales > 0 else 0

            rows.append((
                lid, sku, platform, snap_date,
                sessions, page_views, units_ordered, round(conv_rate, 2),
                buy_box_pct, bsr, cat_rank,
                review_count, rating, new_reviews,
                ad_spend, ad_sales, acos,
            ))

    return insert_rows("listing_performance", columns, rows)


# ---------------------------------------------------------------------------
# 5. replenishment_plans seed data (~50 plans)
# ---------------------------------------------------------------------------
def gen_replenishment_plans() -> str:
    columns = [
        "sku", "warehouse_id", "plan_type", "recommended_qty", "approved_qty",
        "shipping_method_id", "estimated_cost_usd", "status",
        "target_arrival_date", "created_at", "approved_at", "notes",
    ]

    skus = [f"SKU-ERP-{i:03d}" for i in range(1, 31)]
    statuses = ["draft", "approved", "in_procurement", "shipped", "completed", "cancelled"]
    rows = []

    for i in range(50):
        if i == 0:
            # SKU-ERP-015 urgent plan, approved but shipping delayed
            sku = "SKU-ERP-015"
            plan_type = "urgent"
            rec_qty = 500
            app_qty = 500
            method_id = 4  # air express
            cost = round(500 * 0.5 * 45 * 1.35, 2)  # ~weight * cost
            status = "approved"
            target = TODAY - datetime.timedelta(days=5)  # already past target
            created = TODAY - datetime.timedelta(days=15)
            approved = created + datetime.timedelta(days=1)
            notes = "紧急补货-FBA断货风险,物流延迟中"
        elif i == 1:
            # SKU-ERP-023 — no new plans (overstocked), old completed one
            sku = "SKU-ERP-023"
            plan_type = "regular"
            rec_qty = 300
            app_qty = 300
            method_id = 1
            cost = 2500.00
            status = "completed"
            target = TODAY - datetime.timedelta(days=60)
            created = TODAY - datetime.timedelta(days=120)
            approved = created + datetime.timedelta(days=3)
            notes = "已完成-当前库存充足,暂停补货"
        else:
            sku = random.choice(skus)
            # Skip SKU-ERP-023 for new plans
            while sku == "SKU-ERP-023" and status != "completed":
                sku = random.choice(skus)
            plan_type = random.choices(
                ["regular", "urgent", "seasonal"], weights=[0.6, 0.25, 0.15]
            )[0]
            rec_qty = random.randint(100, 2000)
            status = random.choice(statuses)
            if status in ("completed", "shipped", "in_procurement", "approved"):
                app_qty = rec_qty + random.randint(-50, 50)
                app_qty = max(50, app_qty)
            elif status == "cancelled":
                app_qty = None
            else:
                app_qty = None
            method_id = random.randint(1, 8)
            cost = round(random.uniform(500, 15000), 2)
            created = rand_date(
                TODAY - datetime.timedelta(days=150),
                TODAY - datetime.timedelta(days=5),
            )
            target = created + datetime.timedelta(days=random.randint(20, 60))
            if status in ("approved", "in_procurement", "shipped", "completed"):
                approved = created + datetime.timedelta(days=random.randint(1, 5))
            else:
                approved = None
            notes = None

        rows.append((
            sku, random.randint(1, 3), plan_type, rec_qty, app_qty,
            method_id, cost, status, target, created, approved, notes,
        ))

    return insert_rows("replenishment_plans", columns, rows)


# ---------------------------------------------------------------------------
# 6. fba_inventory seed data (~30 SKUs on amazon_us)
# ---------------------------------------------------------------------------
def gen_fba_inventory() -> str:
    columns = [
        "sku", "marketplace", "fulfillable_qty", "inbound_qty",
        "reserved_qty", "unfulfillable_qty", "days_of_supply",
        "restock_needed", "last_updated",
    ]

    rows = []
    for i in range(1, 31):
        sku = f"SKU-ERP-{i:03d}"

        if sku == "SKU-ERP-015":
            # Stockout — matches out_of_stock listing
            fulfillable = 0
            inbound = 500  # urgent replenishment in transit
            reserved = 0
            unfulfillable = 3
            days_supply = 0
            restock = True
        elif sku == "SKU-ERP-023":
            # Overstocked
            fulfillable = 3500
            inbound = 0
            reserved = 20
            unfulfillable = 5
            days_supply = 180
            restock = False
        elif sku in ("SKU-ERP-007", "SKU-ERP-012", "SKU-ERP-028"):
            # High unfulfillable (damaged goods)
            fulfillable = random.randint(50, 200)
            inbound = random.randint(0, 100)
            reserved = random.randint(5, 20)
            unfulfillable = random.randint(40, 80)
            days_supply = random.randint(10, 30)
            restock = True
        else:
            fulfillable = random.randint(30, 800)
            inbound = random.randint(0, 300)
            reserved = random.randint(0, 30)
            unfulfillable = random.randint(0, 10)
            days_supply = random.randint(7, 90)
            restock = days_supply < 15

        last_updated = datetime.datetime.combine(
            TODAY, datetime.time(random.randint(0, 23), random.randint(0, 59))
        )

        rows.append((
            sku, "amazon_us", fulfillable, inbound,
            reserved, unfulfillable, days_supply,
            restock, last_updated,
        ))

    return insert_rows("fba_inventory", columns, rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=== Creating cross-border e-commerce tables ===")
    run_sql(SCHEMA_SQL)
    print("[OK] Tables created")

    print("=== Seeding shipping_methods (8 rows) ===")
    run_sql(gen_shipping_methods())
    print("[OK] shipping_methods seeded")

    print("=== Seeding shipping_costs (~200 rows) ===")
    run_sql(gen_shipping_costs())
    print("[OK] shipping_costs seeded")

    print("=== Seeding listings ===")
    run_sql(gen_listings())
    print("[OK] listings seeded")

    # Count listings for performance data
    out = run_sql("SELECT COUNT(*) FROM listings;")
    listing_count = int(out.strip().split("\n")[-1])
    print(f"    {listing_count} listings found")

    print(f"=== Seeding listing_performance (30 days × {listing_count} listings) ===")
    run_sql(gen_listing_performance(listing_count))
    print("[OK] listing_performance seeded")

    print("=== Seeding replenishment_plans (~50 rows) ===")
    run_sql(gen_replenishment_plans())
    print("[OK] replenishment_plans seeded")

    print("=== Seeding fba_inventory (30 rows) ===")
    run_sql(gen_fba_inventory())
    print("[OK] fba_inventory seeded")

    # Grant permissions
    print("=== Granting SELECT to og_readonly ===")
    run_sql(
        "GRANT SELECT ON orderguard_erp.* TO 'og_readonly'@'localhost';\n"
        "FLUSH PRIVILEGES;"
    )
    print("[OK] Permissions granted")

    # Verification
    print("\n=== Verification ===")
    result = run_sql(
        "SELECT TABLE_NAME, TABLE_ROWS "
        "FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA='orderguard_erp' "
        "ORDER BY TABLE_NAME;"
    )
    print(result)
    print("=== Done ===")


if __name__ == "__main__":
    main()
