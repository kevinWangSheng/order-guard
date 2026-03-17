#!/usr/bin/env python3
"""
seed_pg_analytics.py — 创建分析数据仓库 schema 并填充模拟数据

目标数据库: orderguard_analytics (PostgreSQL)
连接方式: subprocess 调用 psql CLI
"""

import subprocess
import random
import hashlib
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

PSQL = "/opt/homebrew/opt/postgresql@17/bin/psql"
DB = "orderguard_analytics"
USER = "shenghuikevin"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_sql(sql: str, *, db: str = DB) -> None:
    """Execute SQL via psql."""
    result = subprocess.run(
        [PSQL, "-U", USER, "-d", db, "-v", "ON_ERROR_STOP=1"],
        input=sql,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] psql failed:\n{result.stderr}")
        raise RuntimeError(result.stderr)


def sql_str(v) -> str:
    """Escape a value for SQL literal insertion."""
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


def batch_insert(table: str, columns: list[str], rows: list[tuple], conflict: str = "") -> str:
    """Generate batched INSERT statements (500 rows per batch)."""
    col_str = ", ".join(columns)
    stmts = []
    batch_size = 500
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        vals = ",\n".join(
            "(" + ", ".join(sql_str(v) for v in row) + ")" for row in batch
        )
        stmt = f"INSERT INTO {table} ({col_str}) VALUES\n{vals}"
        if conflict:
            stmt += f"\n{conflict}"
        stmt += ";"
        stmts.append(stmt)
    return "\n".join(stmts)


# ---------------------------------------------------------------------------
# Seed data constants
# ---------------------------------------------------------------------------

random.seed(42)

START_DATE = date(2025, 7, 1)
END_DATE = date(2026, 3, 8)

# Chinese holidays (date -> name)
HOLIDAYS = {
    date(2025, 10, 1): "国庆节",
    date(2025, 10, 2): "国庆节",
    date(2025, 10, 3): "国庆节",
    date(2025, 10, 4): "国庆节",
    date(2025, 10, 5): "国庆节",
    date(2025, 10, 6): "国庆节",
    date(2025, 10, 7): "国庆节",
    date(2025, 11, 11): "双十一",
    date(2025, 12, 12): "双十二",
    date(2026, 1, 1): "元旦",
    date(2026, 1, 28): "春节",
    date(2026, 1, 29): "春节",
    date(2026, 1, 30): "春节",
    date(2026, 1, 31): "春节",
    date(2026, 2, 1): "春节",
    date(2026, 2, 2): "春节",
    date(2026, 2, 3): "春节",
    date(2026, 2, 12): "元宵节",
}

BRANDS = ["星耀", "润泽", "碧然", "雅韵", "朗致"]

CATEGORIES = {
    "星耀": ("美妆护肤", "面部护理", ["精华液", "面霜", "洁面乳", "面膜", "防晒霜", "眼霜", "爽肤水", "卸妆油"]),
    "润泽": ("美妆护肤", "身体护理", ["身体乳", "沐浴露", "护手霜", "唇膏", "身体磨砂", "香体喷雾", "足部霜", "身体精油"]),
    "碧然": ("个人护理", "洗护发", ["洗发水", "护发素", "发膜", "生发精华", "头皮护理液", "定型喷雾", "护发精油", "干发喷雾"]),
    "雅韵": ("美妆护肤", "彩妆", ["口红", "粉底液", "眉笔", "睫毛膏", "腮红", "眼影盘", "修容棒", "散粉"]),
    "朗致": ("个人护理", "男士护理", ["男士洁面", "男士面霜", "剃须泡沫", "男士香水", "男士精华", "男士防晒", "须后水", "男士面膜"]),
}

CHANNELS = [
    ("天猫", "电商平台", Decimal("0.05")),
    ("京东", "电商平台", Decimal("0.06")),
    ("抖音", "社交电商", Decimal("0.08")),
    ("拼多多", "电商平台", Decimal("0.03")),
    ("小红书", "社交电商", Decimal("0.07")),
    ("微信商城", "自营", Decimal("0.01")),
    ("线下门店", "线下", Decimal("0.00")),
    ("企业团购", "线下", Decimal("0.02")),
]

CITIES = [
    ("北京", "北京", "一线", "华北"), ("上海", "上海", "一线", "华东"),
    ("广州", "广东", "一线", "华南"), ("深圳", "广东", "一线", "华南"),
    ("杭州", "浙江", "二线", "华东"), ("南京", "江苏", "二线", "华东"),
    ("苏州", "江苏", "二线", "华东"), ("成都", "四川", "二线", "西南"),
    ("重庆", "重庆", "二线", "西南"), ("武汉", "湖北", "二线", "华中"),
    ("长沙", "湖南", "二线", "华中"), ("西安", "陕西", "二线", "西北"),
    ("天津", "天津", "二线", "华北"), ("郑州", "河南", "二线", "华中"),
    ("青岛", "山东", "二线", "华北"), ("大连", "辽宁", "二线", "东北"),
    ("沈阳", "辽宁", "二线", "东北"), ("哈尔滨", "黑龙江", "二线", "东北"),
    ("合肥", "安徽", "二线", "华东"), ("福州", "福建", "二线", "华东"),
    ("厦门", "福建", "二线", "华东"), ("昆明", "云南", "三线", "西南"),
    ("贵阳", "贵州", "三线", "西南"), ("南宁", "广西", "三线", "华南"),
    ("太原", "山西", "三线", "华北"), ("兰州", "甘肃", "三线", "西北"),
    ("呼和浩特", "内蒙古", "三线", "华北"), ("乌鲁木齐", "新疆", "四线", "西北"),
    ("银川", "宁夏", "四线", "西北"), ("拉萨", "西藏", "四线", "西南"),
]

PAYMENT_METHODS = ["支付宝", "微信", "银行卡", "花呗"]
ORDER_STATUSES = ["已完成", "已发货", "已退货", "已取消"]
WAREHOUSES = ["华东仓-上海", "华南仓-广州", "华北仓-北京"]


# ---------------------------------------------------------------------------
# Generate dim_product rows
# ---------------------------------------------------------------------------

def gen_products() -> list[tuple]:
    rows = []
    for brand, (cat_l1, cat_l2, items) in CATEGORIES.items():
        for idx, item_name in enumerate(items, 1):
            sku = f"SKU-{brand[0]}{brand[-1]}-{idx:03d}"
            # Make it ASCII-safe for sku PK
            sku_code = f"SKU-{''.join(chr(ord(c) % 26 + 65) for c in brand)}-{idx:03d}"
            # Use a readable sku
            sku_prefix = {"星耀": "XY", "润泽": "RZ", "碧然": "BR", "雅韵": "YY", "朗致": "LZ"}[brand]
            sku_code = f"SKU-{sku_prefix}-{idx:03d}"

            # Special: SKU-AN-012 is the one with margin anomaly — but let's use a
            # recognizable code. We'll designate SKU-BR-004 as the anomaly SKU if needed,
            # but the prompt says "SKU-AN-012". Let's just add it.
            unit_cost = Decimal(str(round(random.uniform(15, 80), 2)))
            markup = Decimal(str(round(random.uniform(1.5, 3.0), 2)))
            unit_price = (unit_cost * markup).quantize(Decimal("0.01"))
            launch = START_DATE - timedelta(days=random.randint(30, 365))
            status = "active"
            name = f"{brand}{item_name}"
            cat_l3 = item_name
            rows.append((sku_code, name, brand, cat_l1, cat_l2, cat_l3,
                          unit_cost, unit_price, launch, status))
    # Add the special anomaly SKU
    rows.append(("SKU-AN-012", "碧然特效发膜", "碧然", "个人护理", "洗护发", "发膜",
                  Decimal("45.00"), Decimal("128.00"), date(2025, 3, 1), "active"))
    return rows


# ---------------------------------------------------------------------------
# Generate dim_date rows
# ---------------------------------------------------------------------------

def gen_dates() -> list[tuple]:
    rows = []
    d = START_DATE
    while d <= END_DATE:
        is_weekend = d.weekday() >= 5
        holiday_name = HOLIDAYS.get(d)
        is_holiday = holiday_name is not None
        rows.append((
            d, d.year, (d.month - 1) // 3 + 1, d.month,
            d.isocalendar()[1], d.weekday(),
            is_weekend, is_holiday, holiday_name,
        ))
        d += timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Generate fact_daily_sales
# ---------------------------------------------------------------------------

def gen_daily_sales(products, channels, regions, dates) -> list[tuple]:
    rows = []
    sku_list = [p[0] for p in products]
    sku_cost = {p[0]: float(p[6]) for p in products}
    sku_price = {p[0]: float(p[7]) for p in products}
    sku_brand = {p[0]: p[2] for p in products}
    channel_ids = list(range(1, len(channels) + 1))
    channel_names = {i + 1: channels[i][0] for i in range(len(channels))}
    region_ids = list(range(1, len(regions) + 1))
    region_groups = {i + 1: regions[i][3] for i in range(len(regions)) if i < len(regions)}

    for d_row in dates:
        d = d_row[0]
        month = d.month
        year = d.year
        is_weekend = d_row[5]
        day_of_week = d_row[5]

        # Seasonal multipliers
        season_mult = 1.0
        if d == date(2025, 11, 11):
            season_mult = 8.0  # 双十一 spike
        elif date(2025, 11, 10) <= d <= date(2025, 11, 12):
            season_mult = 4.0
        elif d == date(2025, 12, 12):
            season_mult = 5.0  # 双十二 spike
        elif date(2025, 12, 11) <= d <= date(2025, 12, 13):
            season_mult = 3.0
        elif date(2026, 1, 28) <= d <= date(2026, 2, 3):
            season_mult = 0.3  # 春节 dip
        elif date(2026, 2, 4) <= d <= date(2026, 2, 10):
            season_mult = 0.5  # post-春节 recovery

        # Gradual growth: ~1% per month from baseline
        months_from_start = (d.year - 2025) * 12 + d.month - 7
        growth_mult = 1.0 + 0.01 * months_from_start

        # Weekend bump
        weekend_mult = 1.3 if is_weekend else 1.0

        # Sample subset of SKUs × channels × regions per day
        num_combos = int(160 * season_mult * weekend_mult)
        num_combos = max(70, min(num_combos, 1000))

        for _ in range(num_combos):
            sku = random.choice(sku_list)
            ch_id = random.choice(channel_ids)
            rg_id = random.choice(region_ids)
            brand = sku_brand[sku]

            base_orders = random.randint(1, 15)
            order_count = max(1, int(base_orders * season_mult * growth_mult * weekend_mult))

            # --- ANOMALY: 星耀 brand March sales drop 60% ---
            if brand == "星耀" and year == 2026 and month == 3:
                order_count = max(1, int(order_count * 0.4))

            # --- ANOMALY: 华北 Feb orders drop 80% ---
            rg_group = region_groups.get(rg_id, "")
            if rg_group == "华北" and year == 2026 and month == 2:
                order_count = max(1, int(order_count * 0.2))

            quantity = order_count * random.randint(1, 3)
            price = sku_price[sku]
            cost = sku_cost[sku]

            # --- ANOMALY: SKU-AN-012 margin collapse in 2026 ---
            if sku == "SKU-AN-012" and year == 2026 and month >= 2:
                # Selling below cost
                price = cost * 0.65

            discount_rate = random.uniform(0, 0.15)
            gmv = round(quantity * price, 2)
            discount_amount = round(gmv * discount_rate, 2)
            revenue = round(gmv - discount_amount, 2)
            total_cost = round(quantity * cost, 2)

            # Returns
            # --- ANOMALY: 抖音 return rate 18% ---
            ch_name = channel_names.get(ch_id, "")
            if ch_name == "抖音":
                return_rate = 0.18
            else:
                return_rate = 0.04
            return_count = int(quantity * return_rate * random.uniform(0.5, 1.5))
            return_amount = round(return_count * price * (1 - discount_rate), 2)

            rows.append((
                d, sku, ch_id, rg_id,
                order_count, quantity,
                Decimal(str(gmv)), Decimal(str(revenue)),
                Decimal(str(total_cost)), Decimal(str(discount_amount)),
                return_count, Decimal(str(return_amount)),
            ))

    return rows


# ---------------------------------------------------------------------------
# Generate fact_inventory_snapshot
# ---------------------------------------------------------------------------

def gen_inventory(products) -> list[tuple]:
    rows = []
    sku_list = [p[0] for p in products]
    d = START_DATE
    week = 0
    while d <= END_DATE:
        # Weekly snapshots (Sundays)
        if d.weekday() == 6:
            week += 1
            for sku in sku_list:
                for wh in WAREHOUSES:
                    base_qty = random.randint(200, 2000)
                    # --- ANOMALY: post-双十一 inventory buildup ---
                    if date(2025, 11, 15) <= d <= date(2025, 12, 31):
                        base_qty = int(base_qty * 1.8)
                    reserved = random.randint(0, base_qty // 5)
                    available = base_qty - reserved
                    # Days of stock
                    daily_sales = max(1, random.randint(5, 30))
                    days_of_stock = available // daily_sales

                    # --- ANOMALY: turnover days spike after 双十一 ---
                    if date(2025, 11, 20) <= d <= date(2025, 12, 15):
                        turnover_days = random.randint(35, 55)
                    else:
                        turnover_days = random.randint(10, 20)

                    rows.append((
                        d, sku, wh, base_qty, reserved, available,
                        days_of_stock, turnover_days,
                    ))
        d += timedelta(days=1)
    return rows


# ---------------------------------------------------------------------------
# Generate fact_customer_orders
# ---------------------------------------------------------------------------

def gen_customer_orders(products, channels, regions, dates) -> list[tuple]:
    rows = []
    sku_list = [p[0] for p in products]
    sku_price = {p[0]: float(p[7]) for p in products}
    sku_cost = {p[0]: float(p[6]) for p in products}
    sku_brand = {p[0]: p[2] for p in products}

    order_counter = 0
    customer_pool = [f"C{i:06d}" for i in range(1, 3001)]
    new_customer_idx = 0  # track "new" customers

    for d_row in dates:
        d = d_row[0]
        year = d.year
        month = d.month

        # Base orders per day
        base = random.randint(20, 40)
        season_mult = 1.0
        if d == date(2025, 11, 11):
            season_mult = 6.0
        elif date(2025, 11, 10) <= d <= date(2025, 11, 12):
            season_mult = 3.0
        elif d == date(2025, 12, 12):
            season_mult = 4.0
        elif date(2026, 1, 28) <= d <= date(2026, 2, 3):
            season_mult = 0.3

        num_orders = max(3, int(base * season_mult))

        for _ in range(num_orders):
            order_counter += 1
            order_id = f"ORD-{d.strftime('%Y%m%d')}-{order_counter:06d}"
            sku = random.choice(sku_list)
            ch_id = random.randint(1, len(channels))
            rg_id = random.randint(1, len(regions))
            quantity = random.randint(1, 5)
            price = sku_price[sku]
            cost = sku_cost[sku]
            brand = sku_brand[sku]

            # SKU-AN-012 anomaly
            if sku == "SKU-AN-012" and year == 2026 and month >= 2:
                price = cost * 0.65

            discount = round(random.uniform(0, price * 0.15 * quantity), 2)
            amount = round(quantity * price - discount, 2)

            payment = random.choice(PAYMENT_METHODS)

            # Status
            ch_name = channels[ch_id - 1][0] if ch_id <= len(channels) else "天猫"
            if ch_name == "抖音" and random.random() < 0.18:
                status = "已退货"
            elif random.random() < 0.04:
                status = "已退货"
            elif random.random() < 0.02:
                status = "已取消"
            else:
                status = "已完成"

            delivery_days = random.randint(1, 7) if status in ("已完成", "已退货") else None

            # --- ANOMALY: March week 1 new customer ratio drop ---
            # We'll pick "new" vs "repeat" customers
            if year == 2026 and month == 3 and d.day <= 7:
                # Only 12% new
                is_new = random.random() < 0.12
            else:
                is_new = random.random() < 0.40

            if is_new:
                new_customer_idx = (new_customer_idx + 1) % len(customer_pool)
                customer_id = f"NEW-{d.strftime('%Y%m%d')}-{random.randint(1,9999):04d}"
            else:
                customer_id = random.choice(customer_pool)

            rows.append((
                d, order_id, customer_id, ch_id, rg_id, sku,
                quantity, Decimal(str(amount)), Decimal(str(discount)),
                payment, status, delivery_days,
            ))

    return rows


# ---------------------------------------------------------------------------
# Generate agg_weekly_kpi
# ---------------------------------------------------------------------------

def gen_weekly_kpi(dates) -> list[tuple]:
    rows = []
    # Group dates by ISO week
    weeks = {}
    for d_row in dates:
        d = d_row[0]
        iso = d.isocalendar()
        key = (iso[0], iso[1])
        weeks.setdefault(key, []).append(d)

    for (yr, wk), days in sorted(weeks.items()):
        week_start = min(days)
        week_end = max(days)
        month = week_start.month
        year = week_start.year

        base_gmv = random.uniform(150000, 350000)
        base_orders = random.randint(800, 2000)

        # Seasonal
        season = 1.0
        if any(date(2025, 11, 10) <= d <= date(2025, 11, 12) for d in days):
            season = 5.0
        elif any(date(2025, 12, 11) <= d <= date(2025, 12, 13) for d in days):
            season = 3.5
        elif any(date(2026, 1, 28) <= d <= date(2026, 2, 3) for d in days):
            season = 0.3

        total_gmv = round(base_gmv * season, 2)
        total_orders = max(50, int(base_orders * season))
        avg_order_value = round(total_gmv / total_orders, 2)
        return_rate = round(random.uniform(0.03, 0.08), 4)

        # --- ANOMALY: March week 1 new customer drop ---
        if year == 2026 and month == 3 and week_start.day <= 7:
            new_cust = random.randint(30, 60)
            repeat_cust = random.randint(300, 500)
        else:
            new_cust = random.randint(150, 350)
            repeat_cust = random.randint(250, 500)

        # --- ANOMALY: post-双十一 inventory turnover spike ---
        if date(2025, 11, 15) <= week_start <= date(2025, 12, 15):
            inv_turnover = round(random.uniform(35, 50), 1)
        else:
            inv_turnover = round(random.uniform(12, 20), 1)

        rows.append((
            week_start, week_end,
            Decimal(str(total_gmv)), total_orders,
            Decimal(str(avg_order_value)),
            Decimal(str(return_rate)),
            new_cust, repeat_cust,
            Decimal(str(inv_turnover)),
        ))
    return rows


# ---------------------------------------------------------------------------
# Generate agg_sku_monthly
# ---------------------------------------------------------------------------

def gen_sku_monthly(products) -> list[tuple]:
    rows = []
    sku_list = [p[0] for p in products]
    sku_cost = {p[0]: float(p[6]) for p in products}
    sku_price = {p[0]: float(p[7]) for p in products}
    sku_brand = {p[0]: p[2] for p in products}

    d = START_DATE
    months = []
    while d <= END_DATE:
        m_key = date(d.year, d.month, 1)
        if m_key not in months:
            months.append(m_key)
        d += timedelta(days=32)
        d = date(d.year, d.month, 1)

    for m in months:
        for sku in sku_list:
            price = sku_price[sku]
            cost = sku_cost[sku]
            brand = sku_brand[sku]

            base_qty = random.randint(200, 1500)

            # --- ANOMALY: 星耀 March drop ---
            if brand == "星耀" and m.year == 2026 and m.month == 3:
                base_qty = int(base_qty * 0.4)

            total_quantity = base_qty
            effective_price = price

            # --- ANOMALY: SKU-AN-012 margin collapse ---
            if sku == "SKU-AN-012" and m.year == 2026 and m.month >= 2:
                effective_price = cost * 0.65

            total_revenue = round(total_quantity * effective_price * random.uniform(0.85, 0.95), 2)
            total_cost = round(total_quantity * cost, 2)
            gross_margin = round((total_revenue - total_cost) / max(total_revenue, 1) * 100, 2)
            return_rate = round(random.uniform(0.02, 0.06), 4)
            avg_daily = round(total_quantity / 30, 1)

            # Stock coverage
            if date(2025, 11, 1) <= m <= date(2025, 12, 1):
                stock_days = random.randint(30, 50)
            else:
                stock_days = random.randint(10, 25)

            rows.append((
                m, sku, total_quantity,
                Decimal(str(total_revenue)), Decimal(str(total_cost)),
                Decimal(str(gross_margin)), Decimal(str(return_rate)),
                Decimal(str(avg_daily)), stock_days,
            ))
    return rows


# ===========================================================================
# DDL
# ===========================================================================

DDL = """
-- dim_date
CREATE TABLE IF NOT EXISTS dim_date (
    date_key     DATE PRIMARY KEY,
    year         INT NOT NULL,
    quarter      INT NOT NULL,
    month        INT NOT NULL,
    week         INT NOT NULL,
    day_of_week  INT NOT NULL,
    is_weekend   BOOLEAN NOT NULL DEFAULT FALSE,
    is_holiday   BOOLEAN NOT NULL DEFAULT FALSE,
    holiday_name VARCHAR(50)
);
COMMENT ON TABLE dim_date IS '日期维度表';

-- dim_product
CREATE TABLE IF NOT EXISTS dim_product (
    sku          VARCHAR(20) PRIMARY KEY,
    name         VARCHAR(100) NOT NULL,
    brand        VARCHAR(50) NOT NULL,
    category_l1  VARCHAR(50) NOT NULL,
    category_l2  VARCHAR(50) NOT NULL,
    category_l3  VARCHAR(50) NOT NULL,
    unit_cost    NUMERIC(10,2) NOT NULL,
    unit_price   NUMERIC(10,2) NOT NULL,
    launch_date  DATE,
    status       VARCHAR(20) NOT NULL DEFAULT 'active'
);
COMMENT ON TABLE dim_product IS '商品维度表';

-- dim_channel
CREATE TABLE IF NOT EXISTS dim_channel (
    channel_id      SERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL,
    type            VARCHAR(30) NOT NULL,
    commission_rate NUMERIC(5,4) NOT NULL DEFAULT 0,
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
);
COMMENT ON TABLE dim_channel IS '渠道维度表';

-- dim_region
CREATE TABLE IF NOT EXISTS dim_region (
    region_id    SERIAL PRIMARY KEY,
    city         VARCHAR(50) NOT NULL,
    province     VARCHAR(50) NOT NULL,
    tier         VARCHAR(10) NOT NULL,
    region_group VARCHAR(10) NOT NULL
);
COMMENT ON TABLE dim_region IS '地区维度表';

-- fact_daily_sales
CREATE TABLE IF NOT EXISTS fact_daily_sales (
    id              BIGSERIAL PRIMARY KEY,
    date_key        DATE NOT NULL REFERENCES dim_date(date_key),
    sku             VARCHAR(20) NOT NULL REFERENCES dim_product(sku),
    channel_id      INT NOT NULL REFERENCES dim_channel(channel_id),
    region_id       INT NOT NULL REFERENCES dim_region(region_id),
    order_count     INT NOT NULL DEFAULT 0,
    quantity        INT NOT NULL DEFAULT 0,
    gmv             NUMERIC(14,2) NOT NULL DEFAULT 0,
    revenue         NUMERIC(14,2) NOT NULL DEFAULT 0,
    cost            NUMERIC(14,2) NOT NULL DEFAULT 0,
    discount_amount NUMERIC(14,2) NOT NULL DEFAULT 0,
    return_count    INT NOT NULL DEFAULT 0,
    return_amount   NUMERIC(14,2) NOT NULL DEFAULT 0
);
COMMENT ON TABLE fact_daily_sales IS '每日销售事实表';
CREATE INDEX IF NOT EXISTS idx_fds_date ON fact_daily_sales(date_key);
CREATE INDEX IF NOT EXISTS idx_fds_sku ON fact_daily_sales(sku);
CREATE INDEX IF NOT EXISTS idx_fds_channel ON fact_daily_sales(channel_id);
CREATE INDEX IF NOT EXISTS idx_fds_region ON fact_daily_sales(region_id);

-- fact_inventory_snapshot
CREATE TABLE IF NOT EXISTS fact_inventory_snapshot (
    id              BIGSERIAL PRIMARY KEY,
    snapshot_date   DATE NOT NULL,
    sku             VARCHAR(20) NOT NULL REFERENCES dim_product(sku),
    warehouse       VARCHAR(50) NOT NULL,
    quantity        INT NOT NULL DEFAULT 0,
    reserved        INT NOT NULL DEFAULT 0,
    available       INT NOT NULL DEFAULT 0,
    days_of_stock   INT,
    turnover_days   INT
);
COMMENT ON TABLE fact_inventory_snapshot IS '库存快照表';
CREATE INDEX IF NOT EXISTS idx_fis_date ON fact_inventory_snapshot(snapshot_date);
CREATE INDEX IF NOT EXISTS idx_fis_sku ON fact_inventory_snapshot(sku);

-- fact_customer_orders
CREATE TABLE IF NOT EXISTS fact_customer_orders (
    id              BIGSERIAL PRIMARY KEY,
    order_date      DATE NOT NULL,
    order_id        VARCHAR(30) NOT NULL,
    customer_id     VARCHAR(30) NOT NULL,
    channel_id      INT NOT NULL REFERENCES dim_channel(channel_id),
    region_id       INT NOT NULL REFERENCES dim_region(region_id),
    sku             VARCHAR(20) NOT NULL REFERENCES dim_product(sku),
    quantity        INT NOT NULL DEFAULT 1,
    amount          NUMERIC(14,2) NOT NULL DEFAULT 0,
    discount        NUMERIC(14,2) NOT NULL DEFAULT 0,
    payment_method  VARCHAR(20),
    status          VARCHAR(20) NOT NULL DEFAULT '已完成',
    delivery_days   INT
);
COMMENT ON TABLE fact_customer_orders IS '客户订单事实表';
CREATE INDEX IF NOT EXISTS idx_fco_date ON fact_customer_orders(order_date);
CREATE INDEX IF NOT EXISTS idx_fco_customer ON fact_customer_orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_fco_order ON fact_customer_orders(order_id);
CREATE INDEX IF NOT EXISTS idx_fco_sku ON fact_customer_orders(sku);

-- agg_weekly_kpi
CREATE TABLE IF NOT EXISTS agg_weekly_kpi (
    id                    SERIAL PRIMARY KEY,
    week_start            DATE NOT NULL,
    week_end              DATE NOT NULL,
    total_gmv             NUMERIC(14,2) NOT NULL DEFAULT 0,
    total_orders          INT NOT NULL DEFAULT 0,
    avg_order_value       NUMERIC(10,2) NOT NULL DEFAULT 0,
    return_rate           NUMERIC(6,4) NOT NULL DEFAULT 0,
    new_customer_count    INT NOT NULL DEFAULT 0,
    repeat_customer_count INT NOT NULL DEFAULT 0,
    inventory_turnover    NUMERIC(6,1) NOT NULL DEFAULT 0
);
COMMENT ON TABLE agg_weekly_kpi IS '周度KPI汇总表';
CREATE INDEX IF NOT EXISTS idx_wkpi_start ON agg_weekly_kpi(week_start);

-- agg_sku_monthly
CREATE TABLE IF NOT EXISTS agg_sku_monthly (
    id                  SERIAL PRIMARY KEY,
    month               DATE NOT NULL,
    sku                 VARCHAR(20) NOT NULL REFERENCES dim_product(sku),
    total_quantity      INT NOT NULL DEFAULT 0,
    total_revenue       NUMERIC(14,2) NOT NULL DEFAULT 0,
    total_cost          NUMERIC(14,2) NOT NULL DEFAULT 0,
    gross_margin_pct    NUMERIC(6,2) NOT NULL DEFAULT 0,
    return_rate         NUMERIC(6,4) NOT NULL DEFAULT 0,
    avg_daily_sales     NUMERIC(8,1) NOT NULL DEFAULT 0,
    stock_coverage_days INT NOT NULL DEFAULT 0
);
COMMENT ON TABLE agg_sku_monthly IS 'SKU月度汇总表';
CREATE INDEX IF NOT EXISTS idx_skum_month ON agg_sku_monthly(month);
CREATE INDEX IF NOT EXISTS idx_skum_sku ON agg_sku_monthly(sku);
"""

GRANTS = """
GRANT USAGE ON SCHEMA public TO og_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO og_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO og_readonly;
"""


# ===========================================================================
# Main
# ===========================================================================

def main():
    print("=== OrderGuard Analytics Seed Script ===")

    # 1. DDL
    print("[1/8] Creating tables...")
    run_sql(DDL)

    # 2. dim_date
    print("[2/8] Populating dim_date...")
    date_rows = gen_dates()
    sql = batch_insert(
        "dim_date",
        ["date_key", "year", "quarter", "month", "week", "day_of_week",
         "is_weekend", "is_holiday", "holiday_name"],
        date_rows,
        conflict="ON CONFLICT (date_key) DO NOTHING",
    )
    run_sql(sql)
    print(f"       {len(date_rows)} rows")

    # 3. dim_product
    print("[3/8] Populating dim_product...")
    product_rows = gen_products()
    sql = batch_insert(
        "dim_product",
        ["sku", "name", "brand", "category_l1", "category_l2", "category_l3",
         "unit_cost", "unit_price", "launch_date", "status"],
        product_rows,
        conflict="ON CONFLICT (sku) DO NOTHING",
    )
    run_sql(sql)
    print(f"       {len(product_rows)} rows")

    # 4. dim_channel — needs special handling for SERIAL PK
    print("[4/8] Populating dim_channel...")
    # Truncate and re-insert to ensure consistent IDs
    ch_sql = "TRUNCATE dim_channel CASCADE;\n"
    for idx, (name, typ, rate) in enumerate(CHANNELS, 1):
        ch_sql += (
            f"INSERT INTO dim_channel (channel_id, name, type, commission_rate, status) "
            f"VALUES ({idx}, '{name}', '{typ}', {rate}, 'active') "
            f"ON CONFLICT DO NOTHING;\n"
        )
    ch_sql += f"SELECT setval('dim_channel_channel_id_seq', {len(CHANNELS)});\n"
    run_sql(ch_sql)
    print(f"       {len(CHANNELS)} rows")

    # 5. dim_region
    print("[5/8] Populating dim_region...")
    rg_sql = "TRUNCATE dim_region CASCADE;\n"
    for idx, (city, province, tier, group) in enumerate(CITIES, 1):
        rg_sql += (
            f"INSERT INTO dim_region (region_id, city, province, tier, region_group) "
            f"VALUES ({idx}, '{city}', '{province}', '{tier}', '{group}') "
            f"ON CONFLICT DO NOTHING;\n"
        )
    rg_sql += f"SELECT setval('dim_region_region_id_seq', {len(CITIES)});\n"
    run_sql(rg_sql)
    print(f"       {len(CITIES)} rows")

    # 6. fact_daily_sales
    print("[6/8] Generating fact_daily_sales (this may take a moment)...")
    sales_rows = gen_daily_sales(product_rows, CHANNELS, CITIES, date_rows)
    # Truncate for idempotent re-runs
    run_sql("TRUNCATE fact_daily_sales;")
    sql = batch_insert(
        "fact_daily_sales",
        ["date_key", "sku", "channel_id", "region_id",
         "order_count", "quantity", "gmv", "revenue", "cost",
         "discount_amount", "return_count", "return_amount"],
        sales_rows,
    )
    run_sql(sql)
    print(f"       {len(sales_rows)} rows")

    # 7. fact_inventory_snapshot
    print("[7/8] Generating fact_inventory_snapshot...")
    inv_rows = gen_inventory(product_rows)
    run_sql("TRUNCATE fact_inventory_snapshot;")
    sql = batch_insert(
        "fact_inventory_snapshot",
        ["snapshot_date", "sku", "warehouse", "quantity", "reserved",
         "available", "days_of_stock", "turnover_days"],
        inv_rows,
    )
    run_sql(sql)
    print(f"       {len(inv_rows)} rows")

    # 8. fact_customer_orders
    print("[8/8] Generating fact_customer_orders...")
    order_rows = gen_customer_orders(product_rows, CHANNELS, CITIES, date_rows)
    run_sql("TRUNCATE fact_customer_orders;")
    # Split into smaller chunks to avoid command-line size limits
    cols = ["order_date", "order_id", "customer_id", "channel_id", "region_id",
            "sku", "quantity", "amount", "discount", "payment_method", "status",
            "delivery_days"]
    sql = batch_insert("fact_customer_orders", cols, order_rows)
    run_sql(sql)
    print(f"       {len(order_rows)} rows")

    # 9. agg_weekly_kpi
    print("[+] Generating agg_weekly_kpi...")
    kpi_rows = gen_weekly_kpi(date_rows)
    run_sql("TRUNCATE agg_weekly_kpi;")
    sql = batch_insert(
        "agg_weekly_kpi",
        ["week_start", "week_end", "total_gmv", "total_orders",
         "avg_order_value", "return_rate", "new_customer_count",
         "repeat_customer_count", "inventory_turnover"],
        kpi_rows,
    )
    run_sql(sql)
    print(f"       {len(kpi_rows)} rows")

    # 10. agg_sku_monthly
    print("[+] Generating agg_sku_monthly...")
    sku_m_rows = gen_sku_monthly(product_rows)
    run_sql("TRUNCATE agg_sku_monthly;")
    sql = batch_insert(
        "agg_sku_monthly",
        ["month", "sku", "total_quantity", "total_revenue", "total_cost",
         "gross_margin_pct", "return_rate", "avg_daily_sales", "stock_coverage_days"],
        sku_m_rows,
    )
    run_sql(sql)
    print(f"       {len(sku_m_rows)} rows")

    # 11. Grants
    print("[+] Granting SELECT to og_readonly...")
    run_sql(GRANTS)

    print("\n=== Done! Analytics database seeded successfully. ===")

    # Quick summary
    summary_sql = """
    SELECT 'dim_date' AS tbl, COUNT(*) FROM dim_date
    UNION ALL SELECT 'dim_product', COUNT(*) FROM dim_product
    UNION ALL SELECT 'dim_channel', COUNT(*) FROM dim_channel
    UNION ALL SELECT 'dim_region', COUNT(*) FROM dim_region
    UNION ALL SELECT 'fact_daily_sales', COUNT(*) FROM fact_daily_sales
    UNION ALL SELECT 'fact_inventory_snapshot', COUNT(*) FROM fact_inventory_snapshot
    UNION ALL SELECT 'fact_customer_orders', COUNT(*) FROM fact_customer_orders
    UNION ALL SELECT 'agg_weekly_kpi', COUNT(*) FROM agg_weekly_kpi
    UNION ALL SELECT 'agg_sku_monthly', COUNT(*) FROM agg_sku_monthly;
    """
    result = subprocess.run(
        [PSQL, "-U", USER, "-d", DB, "-t"],
        input=summary_sql, capture_output=True, text=True,
    )
    print("\nTable row counts:")
    print(result.stdout)


if __name__ == "__main__":
    main()
