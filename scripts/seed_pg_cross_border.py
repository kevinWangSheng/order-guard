#!/usr/bin/env python3
"""
seed_pg_cross_border.py — 跨境电商分析表创建 + 模拟数据填充

目标数据库: orderguard_analytics (PostgreSQL)
连接方式: subprocess 调用 psql CLI

新增表:
  - dim_shipping           物流维度
  - fact_shipping_costs    物流成本事实表
  - fact_listing_performance  Listing 表现事实表
  - fact_fba_inventory     FBA 库存事实表
  - agg_logistics_monthly  物流月度汇总
"""

import subprocess
import random
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

PSQL = "/opt/homebrew/opt/postgresql@17/bin/psql"
DB = "orderguard_analytics"
USER = "shenghuikevin"

random.seed(2024)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_sql(sql: str) -> str:
    """Execute SQL via psql, return stdout."""
    result = subprocess.run(
        [PSQL, "-U", USER, "-d", DB, "-v", "ON_ERROR_STOP=1"],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] psql failed:\n{result.stderr}")
        raise RuntimeError(result.stderr)
    return result.stdout


def sql_str(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    if isinstance(v, date):
        return f"'{v.isoformat()}'"
    s = str(v).replace("'", "''")
    return f"'{s}'"


def batch_insert(table: str, columns: list[str], rows: list[tuple]) -> str:
    col_str = ", ".join(columns)
    stmts = []
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        vals = ",\n".join(
            "(" + ", ".join(sql_str(v) for v in row) + ")" for row in batch
        )
        stmts.append(f"INSERT INTO {table} ({col_str}) VALUES\n{vals};")
    return "\n".join(stmts)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TODAY = date(2026, 3, 8)

SHIPPING_METHODS = [
    # (name, type, route, transit_days_avg, cost_per_kg_usd)
    ("海运普船-美西", "sea", "深圳→美西", 28, 1.20),
    ("海运快船-美西", "sea", "深圳→美西", 18, 2.50),
    ("海运普船-欧洲", "sea", "宁波→汉堡", 35, 1.50),
    ("空运专线-美国", "air", "深圳→洛杉矶", 5, 8.50),
    ("空运专线-欧洲", "air", "上海→法兰克福", 6, 9.00),
    ("国际快递DHL", "express", "全球", 3, 25.00),
    ("国际快递UPS", "express", "全球", 4, 22.00),
    ("中欧班列", "rail", "义乌→杜伊斯堡", 16, 3.80),
]

SKUS = [f"SKU-{str(i).zfill(4)}" for i in range(1, 51)]

# Brand 星耀 SKUs (for rating anomaly)
XINGYAO_SKUS = [f"SKU-{str(i).zfill(4)}" for i in range(1, 11)]

# High ACoS SKUs
HIGH_ACOS_SKUS = [f"SKU-{str(i).zfill(4)}" for i in range(15, 20)]

# BSR deteriorating SKUs
BSR_DETERIORATING_SKUS = [f"SKU-{str(i).zfill(4)}" for i in range(25, 29)]

PLATFORMS = ["amazon_us", "amazon_eu"]
MARKETPLACES = ["US", "DE"]

FBA_SKUS = SKUS[:30]
DEPLETING_SKUS = [f"SKU-{str(i).zfill(4)}" for i in range(35, 40)]
HIGH_UNFULFILLABLE_SKUS = [f"SKU-{str(i).zfill(4)}" for i in range(40, 44)]


# ---------------------------------------------------------------------------
# 1. DDL
# ---------------------------------------------------------------------------

DDL = """
-- dim_shipping
CREATE TABLE IF NOT EXISTS dim_shipping (
  method_id SERIAL PRIMARY KEY,
  name VARCHAR(100) NOT NULL,
  type VARCHAR(20) NOT NULL,
  route VARCHAR(100),
  transit_days_avg INT,
  cost_per_kg_usd NUMERIC(10,2)
);

-- fact_shipping_costs
CREATE TABLE IF NOT EXISTS fact_shipping_costs (
  id BIGSERIAL PRIMARY KEY,
  ship_date DATE NOT NULL,
  sku VARCHAR(50),
  shipping_method_id INT REFERENCES dim_shipping(method_id),
  quantity INT,
  weight_kg NUMERIC(10,2),
  shipping_cost_usd NUMERIC(10,2),
  customs_cost_usd NUMERIC(10,2) DEFAULT 0,
  total_landed_cost_usd NUMERIC(10,2),
  cost_per_unit_usd NUMERIC(10,2),
  transit_days_actual INT,
  status VARCHAR(20) DEFAULT 'delivered'
);

-- fact_listing_performance
CREATE TABLE IF NOT EXISTS fact_listing_performance (
  id BIGSERIAL PRIMARY KEY,
  snapshot_date DATE NOT NULL,
  sku VARCHAR(50) NOT NULL,
  platform VARCHAR(30) NOT NULL,
  sessions INT DEFAULT 0,
  page_views INT DEFAULT 0,
  units_ordered INT DEFAULT 0,
  conversion_rate NUMERIC(5,2),
  bsr_rank INT,
  category_rank INT,
  review_count INT,
  review_rating NUMERIC(3,2),
  buy_box_pct NUMERIC(5,2) DEFAULT 100,
  ad_spend_usd NUMERIC(10,2) DEFAULT 0,
  ad_sales_usd NUMERIC(10,2) DEFAULT 0,
  acos_pct NUMERIC(5,2)
);
CREATE INDEX IF NOT EXISTS idx_flp_sku_date ON fact_listing_performance(sku, snapshot_date);

-- fact_fba_inventory
CREATE TABLE IF NOT EXISTS fact_fba_inventory (
  id BIGSERIAL PRIMARY KEY,
  snapshot_date DATE NOT NULL,
  sku VARCHAR(50) NOT NULL,
  marketplace VARCHAR(20) NOT NULL,
  fulfillable_qty INT DEFAULT 0,
  inbound_qty INT DEFAULT 0,
  reserved_qty INT DEFAULT 0,
  unfulfillable_qty INT DEFAULT 0,
  estimated_days_of_supply INT,
  restock_recommended BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_fba_sku_date ON fact_fba_inventory(sku, snapshot_date);

-- agg_logistics_monthly
CREATE TABLE IF NOT EXISTS agg_logistics_monthly (
  id SERIAL PRIMARY KEY,
  month DATE NOT NULL,
  shipping_type VARCHAR(20),
  total_shipments INT,
  total_weight_kg NUMERIC(12,2),
  total_cost_usd NUMERIC(12,2),
  avg_cost_per_kg NUMERIC(10,2),
  avg_transit_days NUMERIC(5,1),
  delay_rate_pct NUMERIC(5,1),
  on_time_rate_pct NUMERIC(5,1)
);
"""

# ---------------------------------------------------------------------------
# 2. dim_shipping seed
# ---------------------------------------------------------------------------

def seed_dim_shipping() -> str:
    rows = []
    for name, stype, route, days, cost in SHIPPING_METHODS:
        rows.append((name, stype, route, days, cost))
    return batch_insert(
        "dim_shipping",
        ["name", "type", "route", "transit_days_avg", "cost_per_kg_usd"],
        rows,
    )


# ---------------------------------------------------------------------------
# 3. fact_shipping_costs seed (~500 records, 6 months)
# ---------------------------------------------------------------------------

def seed_shipping_costs() -> str:
    start = TODAY - timedelta(days=180)
    rows = []
    # method_id 1-8 correspond to SHIPPING_METHODS order
    air_method_ids = [4, 5]  # air methods
    delay_routes = [3, 8]    # sea-europe, rail

    for _ in range(500):
        days_offset = random.randint(0, 179)
        ship_date = start + timedelta(days=days_offset)
        sku = random.choice(SKUS[:30])
        method_idx = random.randint(0, 7)
        method_id = method_idx + 1
        _, stype, _, transit_avg, base_cost_kg = SHIPPING_METHODS[method_idx]

        quantity = random.randint(50, 2000)
        weight_kg = round(quantity * random.uniform(0.1, 0.8), 2)

        # Anomaly: air freight cost +35% in last 30 days
        cost_kg = base_cost_kg
        if method_id in air_method_ids and ship_date >= (TODAY - timedelta(days=30)):
            cost_kg = round(base_cost_kg * 1.35, 2)

        shipping_cost = round(float(weight_kg) * float(cost_kg), 2)
        customs_cost = round(shipping_cost * random.uniform(0.02, 0.08), 2)
        total_landed = round(shipping_cost + customs_cost, 2)
        cost_per_unit = round(total_landed / quantity, 2) if quantity > 0 else 0

        # Transit days anomaly: some routes delayed 50%+
        transit_actual = transit_avg + random.randint(-2, 3)
        status = "delivered"
        if method_id in delay_routes and random.random() < 0.3:
            transit_actual = int(transit_avg * random.uniform(1.5, 2.0))
            status = "delayed"
        elif ship_date >= (TODAY - timedelta(days=5)):
            status = "in_transit"
            transit_actual = None

        rows.append((
            ship_date, sku, method_id, quantity, weight_kg,
            shipping_cost, customs_cost, total_landed, cost_per_unit,
            transit_actual, status,
        ))

    return batch_insert(
        "fact_shipping_costs",
        ["ship_date", "sku", "shipping_method_id", "quantity", "weight_kg",
         "shipping_cost_usd", "customs_cost_usd", "total_landed_cost_usd",
         "cost_per_unit_usd", "transit_days_actual", "status"],
        rows,
    )


# ---------------------------------------------------------------------------
# 4. fact_listing_performance seed (60 days × 40 SKUs × 2 platforms)
# ---------------------------------------------------------------------------

def seed_listing_performance() -> str:
    start = TODAY - timedelta(days=59)
    listing_skus = SKUS[:40]
    rows = []

    for day_offset in range(60):
        snap_date = start + timedelta(days=day_offset)
        progress = day_offset / 59.0  # 0.0 → 1.0

        for sku in listing_skus:
            for platform in PLATFORMS:
                sessions = random.randint(100, 3000)
                page_views = int(sessions * random.uniform(1.2, 2.0))

                # Base conversion & rating
                base_conv = random.uniform(5, 18)
                base_rating = round(random.uniform(4.2, 4.8), 2)
                review_count = random.randint(50, 2000)

                # --- Anomaly: 星耀 brand rating drop 4.5 → 3.2 ---
                if sku in XINGYAO_SKUS:
                    base_rating = round(4.5 - (1.3 * progress), 2)
                    base_rating = max(base_rating, 3.0)
                    # Conversion correlates with rating drop
                    base_conv = base_conv * (0.5 + 0.5 * (base_rating - 3.0) / 1.5)

                conversion_rate = round(base_conv, 2)
                units_ordered = max(1, int(sessions * conversion_rate / 100))

                # BSR
                bsr_base = random.randint(500, 50000)
                category_rank = random.randint(10, 500)
                # --- Anomaly: BSR deteriorating 10x in 2 weeks ---
                if sku in BSR_DETERIORATING_SKUS and day_offset >= 46:
                    bsr_base = int(bsr_base * (1 + 9 * ((day_offset - 46) / 14)))

                # Buy box
                buy_box = round(random.uniform(85, 100), 2)

                # Ad spend
                ad_spend = round(random.uniform(10, 200), 2)
                ad_sales = round(ad_spend * random.uniform(1.5, 5.0), 2)
                # --- Anomaly: high ACoS > 60% ---
                if sku in HIGH_ACOS_SKUS:
                    ad_sales = round(ad_spend * random.uniform(0.3, 0.7), 2)

                acos = round((ad_spend / ad_sales * 100) if ad_sales > 0 else 0, 2)

                rows.append((
                    snap_date, sku, platform, sessions, page_views,
                    units_ordered, conversion_rate, bsr_base, category_rank,
                    review_count, base_rating, buy_box, ad_spend, ad_sales, acos,
                ))

    return batch_insert(
        "fact_listing_performance",
        ["snapshot_date", "sku", "platform", "sessions", "page_views",
         "units_ordered", "conversion_rate", "bsr_rank", "category_rank",
         "review_count", "review_rating", "buy_box_pct", "ad_spend_usd",
         "ad_sales_usd", "acos_pct"],
        rows,
    )


# ---------------------------------------------------------------------------
# 5. fact_fba_inventory seed (weekly × 30 SKUs × 8 weeks)
# ---------------------------------------------------------------------------

def seed_fba_inventory() -> str:
    rows = []
    for week in range(8):
        snap_date = TODAY - timedelta(weeks=7 - week)
        progress = week / 7.0  # 0→1 over 8 weeks

        for sku in FBA_SKUS:
            for mp in MARKETPLACES:
                base_qty = random.randint(200, 1500)
                inbound = random.randint(0, 300)
                reserved = random.randint(10, 80)
                unfulfillable = random.randint(0, 10)

                # --- Anomaly: depleting SKUs (days_of_supply 30 → 5) ---
                if sku in DEPLETING_SKUS:
                    base_qty = max(5, int(1200 * (1 - 0.85 * progress)))
                    days_supply = max(5, int(30 * (1 - progress * 0.83)))
                else:
                    days_supply = random.randint(15, 60)

                # --- Anomaly: high unfulfillable ---
                if sku in HIGH_UNFULFILLABLE_SKUS:
                    unfulfillable = random.randint(80, 300)

                restock = days_supply <= 14

                rows.append((
                    snap_date, sku, mp, base_qty, inbound, reserved,
                    unfulfillable, days_supply, restock,
                ))

    return batch_insert(
        "fact_fba_inventory",
        ["snapshot_date", "sku", "marketplace", "fulfillable_qty", "inbound_qty",
         "reserved_qty", "unfulfillable_qty", "estimated_days_of_supply",
         "restock_recommended"],
        rows,
    )


# ---------------------------------------------------------------------------
# 6. agg_logistics_monthly seed (8 months)
# ---------------------------------------------------------------------------

def seed_logistics_monthly() -> str:
    rows = []
    for m in range(8):
        month_date = date(2025, 8 + m if 8 + m <= 12 else (8 + m - 12), 1)
        if 8 + m > 12:
            month_date = month_date.replace(year=2026)

        for stype in ["sea", "air", "express", "rail"]:
            shipments = random.randint(30, 150)
            total_weight = round(random.uniform(5000, 50000), 2)

            # Air freight cost trending up
            if stype == "air":
                base_cost_kg = 8.50 + m * 0.60  # increasing each month
            elif stype == "sea":
                base_cost_kg = random.uniform(1.2, 2.5)
            elif stype == "express":
                base_cost_kg = random.uniform(20, 26)
            else:  # rail
                base_cost_kg = random.uniform(3.5, 4.2)

            avg_cost_kg = round(base_cost_kg, 2)
            total_cost = round(total_weight * avg_cost_kg, 2)

            if stype == "sea":
                avg_transit = round(random.uniform(18, 35), 1)
            elif stype == "air":
                avg_transit = round(random.uniform(4, 7), 1)
            elif stype == "express":
                avg_transit = round(random.uniform(2, 5), 1)
            else:
                avg_transit = round(random.uniform(14, 18), 1)

            delay_rate = round(random.uniform(3, 15), 1)
            on_time = round(100 - delay_rate, 1)

            rows.append((
                month_date, stype, shipments, total_weight, total_cost,
                avg_cost_kg, avg_transit, delay_rate, on_time,
            ))

    return batch_insert(
        "agg_logistics_monthly",
        ["month", "shipping_type", "total_shipments", "total_weight_kg",
         "total_cost_usd", "avg_cost_per_kg", "avg_transit_days",
         "delay_rate_pct", "on_time_rate_pct"],
        rows,
    )


# ---------------------------------------------------------------------------
# 7. Grants
# ---------------------------------------------------------------------------

GRANTS = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'og_readonly') THEN
    GRANT USAGE ON SCHEMA public TO og_readonly;
    GRANT SELECT ON ALL TABLES IN SCHEMA public TO og_readonly;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO og_readonly;
    RAISE NOTICE 'Grants applied to og_readonly';
  ELSE
    RAISE NOTICE 'Role og_readonly does not exist — skipping grants';
  END IF;
END
$$;
"""

# ---------------------------------------------------------------------------
# 8. Verify
# ---------------------------------------------------------------------------

VERIFY_SQL = """
SELECT 'dim_shipping' AS tbl, COUNT(*) AS cnt FROM dim_shipping
UNION ALL
SELECT 'fact_shipping_costs', COUNT(*) FROM fact_shipping_costs
UNION ALL
SELECT 'fact_listing_performance', COUNT(*) FROM fact_listing_performance
UNION ALL
SELECT 'fact_fba_inventory', COUNT(*) FROM fact_fba_inventory
UNION ALL
SELECT 'agg_logistics_monthly', COUNT(*) FROM agg_logistics_monthly;
"""

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=== Creating tables (DDL) ===")
    run_sql(DDL)
    print("  Tables created.")

    print("=== Seeding dim_shipping (8 methods) ===")
    run_sql(seed_dim_shipping())
    print("  Done.")

    print("=== Seeding fact_shipping_costs (~500 rows) ===")
    run_sql(seed_shipping_costs())
    print("  Done.")

    print("=== Seeding fact_listing_performance (~4800 rows) ===")
    run_sql(seed_listing_performance())
    print("  Done.")

    print("=== Seeding fact_fba_inventory (~240+ rows) ===")
    run_sql(seed_fba_inventory())
    print("  Done.")

    print("=== Seeding agg_logistics_monthly (8 months) ===")
    run_sql(seed_logistics_monthly())
    print("  Done.")

    print("=== Applying grants ===")
    run_sql(GRANTS)
    print("  Done.")

    print("\n=== Verification: Row counts ===")
    out = run_sql(VERIFY_SQL)
    print(out)

    print("All cross-border e-commerce tables seeded successfully!")


if __name__ == "__main__":
    main()
