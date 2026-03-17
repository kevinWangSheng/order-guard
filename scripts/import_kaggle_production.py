#!/usr/bin/env python3
"""
Import Kaggle public datasets into MySQL to simulate a production e-commerce environment.

Datasets imported:
1. Olist Brazilian E-Commerce (9 tables, 100K orders) - core ERP data
2. Product Sales & Returns (70K records) - return/refund analysis
3. Retail Inventory Forecasting (73K records) - inventory levels & demand

Usage:
    uv run python scripts/import_kaggle_production.py

Creates database: orderguard_prod
Read-only user: og_readonly (reuses existing password)
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import mysql.connector

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DB_HOST = "127.0.0.1"
DB_USER = "root"
DB_PASS = ""
DB_NAME = "orderguard_prod"
READONLY_USER = "og_readonly"
READONLY_PASS = "og_test_2026"

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "kaggle"
OLIST_DIR = DATA_DIR / "olist"
RETURNS_DIR = DATA_DIR / "returns"
INVENTORY_DIR = DATA_DIR / "inventory-forecast"


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
-- -------------------------------------------------------
-- 1. Olist: customers
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    customer_id             VARCHAR(40) PRIMARY KEY,
    customer_unique_id      VARCHAR(40) NOT NULL,
    customer_zip_code       VARCHAR(10),
    customer_city           VARCHAR(100),
    customer_state          VARCHAR(5),
    INDEX idx_unique_id (customer_unique_id),
    INDEX idx_state (customer_state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 2. Olist: sellers
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS sellers (
    seller_id               VARCHAR(40) PRIMARY KEY,
    seller_zip_code         VARCHAR(10),
    seller_city             VARCHAR(100),
    seller_state            VARCHAR(5),
    INDEX idx_state (seller_state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 3. Olist: product_categories (translation)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_categories (
    category_name           VARCHAR(100) PRIMARY KEY,
    category_name_english   VARCHAR(100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 4. Olist: products
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    product_id              VARCHAR(40) PRIMARY KEY,
    product_category_name   VARCHAR(100),
    product_name_length     INT,
    product_description_length INT,
    product_photos_qty      INT,
    product_weight_g        INT,
    product_length_cm       INT,
    product_height_cm       INT,
    product_width_cm        INT,
    INDEX idx_category (product_category_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 5. Olist: orders
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
    order_id                    VARCHAR(40) PRIMARY KEY,
    customer_id                 VARCHAR(40) NOT NULL,
    order_status                VARCHAR(20) NOT NULL,
    order_purchase_timestamp    DATETIME,
    order_approved_at           DATETIME,
    order_delivered_carrier_date DATETIME,
    order_delivered_customer_date DATETIME,
    order_estimated_delivery_date DATE,
    INDEX idx_customer (customer_id),
    INDEX idx_status (order_status),
    INDEX idx_purchase_date (order_purchase_timestamp),
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 6. Olist: order_items
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_items (
    order_id            VARCHAR(40) NOT NULL,
    order_item_id       INT NOT NULL,
    product_id          VARCHAR(40) NOT NULL,
    seller_id           VARCHAR(40) NOT NULL,
    shipping_limit_date DATETIME,
    price               DECIMAL(10,2) NOT NULL,
    freight_value       DECIMAL(10,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (order_id, order_item_id),
    INDEX idx_product (product_id),
    INDEX idx_seller (seller_id),
    FOREIGN KEY (order_id) REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id),
    FOREIGN KEY (seller_id) REFERENCES sellers(seller_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 7. Olist: order_payments
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_payments (
    order_id            VARCHAR(40) NOT NULL,
    payment_sequential  INT NOT NULL,
    payment_type        VARCHAR(30) NOT NULL,
    payment_installments INT NOT NULL DEFAULT 1,
    payment_value       DECIMAL(10,2) NOT NULL,
    PRIMARY KEY (order_id, payment_sequential),
    INDEX idx_type (payment_type),
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 8. Olist: order_reviews
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS order_reviews (
    review_id               VARCHAR(40) PRIMARY KEY,
    order_id                VARCHAR(40) NOT NULL,
    review_score            TINYINT NOT NULL,
    review_comment_title    TEXT,
    review_comment_message  TEXT,
    review_creation_date    DATETIME,
    review_answer_timestamp DATETIME,
    INDEX idx_order (order_id),
    INDEX idx_score (review_score),
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 9. Returns & Refunds (from Kaggle returns dataset)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales_returns (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    item_name           VARCHAR(100),
    category            VARCHAR(100),
    version             VARCHAR(50),
    item_code           VARCHAR(50),
    item_id             VARCHAR(20),
    buyer_id            VARCHAR(20),
    transaction_id      VARCHAR(30),
    order_date          DATE,
    final_quantity      INT,
    total_revenue       DECIMAL(12,2),
    price_reductions    DECIMAL(12,2) DEFAULT 0,
    refunds             DECIMAL(12,2) DEFAULT 0,
    final_revenue       DECIMAL(12,2),
    sales_tax           DECIMAL(12,2) DEFAULT 0,
    overall_revenue     DECIMAL(12,2),
    refunded_item_count INT DEFAULT 0,
    purchased_item_count INT DEFAULT 0,
    INDEX idx_category (category),
    INDEX idx_date (order_date),
    INDEX idx_item (item_name),
    INDEX idx_buyer (buyer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 10. Inventory Daily Snapshots (from Kaggle inventory forecast)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory_daily (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    snapshot_date   DATE NOT NULL,
    store_id        VARCHAR(10) NOT NULL,
    product_id      VARCHAR(10) NOT NULL,
    category        VARCHAR(50),
    region          VARCHAR(20),
    inventory_level INT NOT NULL,
    units_sold      INT NOT NULL DEFAULT 0,
    units_ordered   INT NOT NULL DEFAULT 0,
    demand_forecast DECIMAL(10,2),
    price           DECIMAL(10,2),
    discount        DECIMAL(5,2) DEFAULT 0,
    weather         VARCHAR(20),
    holiday_promo   TINYINT DEFAULT 0,
    competitor_price DECIMAL(10,2),
    seasonality     VARCHAR(20),
    INDEX idx_date (snapshot_date),
    INDEX idx_store_product (store_id, product_id),
    INDEX idx_category (category),
    INDEX idx_region (region)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 11. Derived: daily_sales_summary (will be populated after import)
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_sales_summary (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    sale_date       DATE NOT NULL,
    product_id      VARCHAR(40) NOT NULL,
    category        VARCHAR(100),
    order_count     INT NOT NULL DEFAULT 0,
    item_count      INT NOT NULL DEFAULT 0,
    total_revenue   DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_freight   DECIMAL(12,2) NOT NULL DEFAULT 0,
    avg_review_score DECIMAL(3,2),
    INDEX idx_date (sale_date),
    INDEX idx_product (product_id),
    INDEX idx_category (category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- -------------------------------------------------------
-- 12. Derived: seller_performance
-- -------------------------------------------------------
CREATE TABLE IF NOT EXISTS seller_performance (
    seller_id           VARCHAR(40) PRIMARY KEY,
    seller_city         VARCHAR(100),
    seller_state        VARCHAR(5),
    total_orders        INT NOT NULL DEFAULT 0,
    total_items         INT NOT NULL DEFAULT 0,
    total_revenue       DECIMAL(12,2) NOT NULL DEFAULT 0,
    avg_review_score    DECIMAL(3,2),
    avg_delivery_days   DECIMAL(5,1),
    late_delivery_pct   DECIMAL(5,2) DEFAULT 0,
    INDEX idx_state (seller_state)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


def connect_root():
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS, charset="utf8mb4"
    )


def connect_db():
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASS,
        database=DB_NAME, charset="utf8mb4",
        allow_local_infile=True,
    )


def create_database(conn):
    cur = conn.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    # Grant readonly access - ignore errors if user doesn't exist yet
    for host in ("%", "localhost"):
        try:
            cur.execute(f"GRANT SELECT ON `{DB_NAME}`.* TO '{READONLY_USER}'@'{host}'")
        except Exception:
            pass
    try:
        cur.execute("FLUSH PRIVILEGES")
    except Exception:
        pass
    conn.commit()
    cur.close()
    print(f"[OK] Database '{DB_NAME}' created")


def create_schema(conn):
    cur = conn.cursor()
    # Split by semicolons, filter out empty/comment-only statements
    for stmt in SCHEMA_SQL.split(";"):
        # Remove comment lines
        lines = [l for l in stmt.strip().split("\n") if not l.strip().startswith("--")]
        cleaned = "\n".join(lines).strip()
        if cleaned:
            try:
                cur.execute(cleaned)
            except Exception as e:
                print(f"  [WARN] Schema stmt failed: {e}")
    conn.commit()
    cur.close()
    print("[OK] Schema created (12 tables)")


def parse_date(s: str) -> str | None:
    """Parse date string, return MySQL compatible format or None."""
    if not s or s.strip() == "":
        return None
    s = s.strip()
    # Handle DD/MM/YYYY format
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3:
            d, m, y = parts
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return s


def parse_float(s: str) -> float | None:
    if not s or s.strip() == "":
        return None
    try:
        return float(s.strip())
    except ValueError:
        return None


def parse_int(s: str) -> int | None:
    if not s or s.strip() == "":
        return None
    try:
        return int(float(s.strip()))
    except ValueError:
        return None


def load_csv(filepath: Path, table: str, columns: list[str], conn,
             transform=None, batch_size=5000):
    """Generic CSV loader with batch INSERT IGNORE."""
    if not filepath.exists():
        print(f"[SKIP] {filepath} not found")
        return 0

    cur = conn.cursor()
    placeholders = ", ".join(["%s"] * len(columns))
    col_names = ", ".join(f"`{c}`" for c in columns)
    sql = f"INSERT IGNORE INTO `{table}` ({col_names}) VALUES ({placeholders})"

    count = 0
    batch = []

    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if transform:
                row = transform(row)
                if row is None:
                    continue
            values = []
            for col in columns:
                v = row.get(col, "")
                if v == "" or v is None:
                    values.append(None)
                else:
                    values.append(v)
            batch.append(tuple(values))
            count += 1

            if len(batch) >= batch_size:
                cur.executemany(sql, batch)
                conn.commit()
                batch = []

    if batch:
        cur.executemany(sql, batch)
        conn.commit()

    cur.close()
    print(f"  [{table}] {count:,} rows loaded")
    return count


def import_olist(conn):
    print("\n=== Importing Olist E-Commerce (9 tables) ===")

    # 1. Product categories
    def tx_category(row):
        return {
            "category_name": row.get("product_category_name"),
            "category_name_english": row.get("product_category_name_english"),
        }
    load_csv(
        OLIST_DIR / "product_category_name_translation.csv",
        "product_categories",
        ["category_name", "category_name_english"],
        conn,
        transform=tx_category,
    )

    # 2. Customers
    def tx_customer(row):
        return {
            "customer_id": row.get("customer_id"),
            "customer_unique_id": row.get("customer_unique_id"),
            "customer_zip_code": row.get("customer_zip_code_prefix"),
            "customer_city": row.get("customer_city"),
            "customer_state": row.get("customer_state"),
        }
    load_csv(
        OLIST_DIR / "olist_customers_dataset.csv",
        "customers",
        ["customer_id", "customer_unique_id", "customer_zip_code", "customer_city", "customer_state"],
        conn,
        transform=tx_customer,
    )

    # 3. Sellers
    def tx_seller(row):
        return {
            "seller_id": row.get("seller_id"),
            "seller_zip_code": row.get("seller_zip_code_prefix"),
            "seller_city": row.get("seller_city"),
            "seller_state": row.get("seller_state"),
        }
    load_csv(
        OLIST_DIR / "olist_sellers_dataset.csv",
        "sellers",
        ["seller_id", "seller_zip_code", "seller_city", "seller_state"],
        conn,
        transform=tx_seller,
    )

    # 4. Products
    def tx_product(row):
        return {
            "product_id": row.get("product_id"),
            "product_category_name": row.get("product_category_name"),
            "product_name_length": parse_int(row.get("product_name_lenght", "")),
            "product_description_length": parse_int(row.get("product_description_lenght", "")),
            "product_photos_qty": parse_int(row.get("product_photos_qty", "")),
            "product_weight_g": parse_int(row.get("product_weight_g", "")),
            "product_length_cm": parse_int(row.get("product_length_cm", "")),
            "product_height_cm": parse_int(row.get("product_height_cm", "")),
            "product_width_cm": parse_int(row.get("product_width_cm", "")),
        }
    load_csv(
        OLIST_DIR / "olist_products_dataset.csv",
        "products",
        ["product_id", "product_category_name", "product_name_length",
         "product_description_length", "product_photos_qty",
         "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"],
        conn,
        transform=tx_product,
    )

    # 5. Orders
    def tx_order(row):
        return {
            "order_id": row.get("order_id"),
            "customer_id": row.get("customer_id"),
            "order_status": row.get("order_status"),
            "order_purchase_timestamp": row.get("order_purchase_timestamp") or None,
            "order_approved_at": row.get("order_approved_at") or None,
            "order_delivered_carrier_date": row.get("order_delivered_carrier_date") or None,
            "order_delivered_customer_date": row.get("order_delivered_customer_date") or None,
            "order_estimated_delivery_date": row.get("order_estimated_delivery_date") or None,
        }
    load_csv(
        OLIST_DIR / "olist_orders_dataset.csv",
        "orders",
        ["order_id", "customer_id", "order_status",
         "order_purchase_timestamp", "order_approved_at",
         "order_delivered_carrier_date", "order_delivered_customer_date",
         "order_estimated_delivery_date"],
        conn,
        transform=tx_order,
    )

    # 6. Order items
    def tx_item(row):
        return {
            "order_id": row.get("order_id"),
            "order_item_id": parse_int(row.get("order_item_id", "1")),
            "product_id": row.get("product_id"),
            "seller_id": row.get("seller_id"),
            "shipping_limit_date": row.get("shipping_limit_date") or None,
            "price": parse_float(row.get("price", "0")),
            "freight_value": parse_float(row.get("freight_value", "0")),
        }
    load_csv(
        OLIST_DIR / "olist_order_items_dataset.csv",
        "order_items",
        ["order_id", "order_item_id", "product_id", "seller_id",
         "shipping_limit_date", "price", "freight_value"],
        conn,
        transform=tx_item,
    )

    # 7. Order payments
    def tx_payment(row):
        return {
            "order_id": row.get("order_id"),
            "payment_sequential": parse_int(row.get("payment_sequential", "1")),
            "payment_type": row.get("payment_type"),
            "payment_installments": parse_int(row.get("payment_installments", "1")),
            "payment_value": parse_float(row.get("payment_value", "0")),
        }
    load_csv(
        OLIST_DIR / "olist_order_payments_dataset.csv",
        "order_payments",
        ["order_id", "payment_sequential", "payment_type",
         "payment_installments", "payment_value"],
        conn,
        transform=tx_payment,
    )

    # 8. Order reviews
    def tx_review(row):
        return {
            "review_id": row.get("review_id"),
            "order_id": row.get("order_id"),
            "review_score": parse_int(row.get("review_score", "0")),
            "review_comment_title": row.get("review_comment_title") or None,
            "review_comment_message": row.get("review_comment_message") or None,
            "review_creation_date": row.get("review_creation_date") or None,
            "review_answer_timestamp": row.get("review_answer_timestamp") or None,
        }
    load_csv(
        OLIST_DIR / "olist_order_reviews_dataset.csv",
        "order_reviews",
        ["review_id", "order_id", "review_score",
         "review_comment_title", "review_comment_message",
         "review_creation_date", "review_answer_timestamp"],
        conn,
        transform=tx_review,
    )


def import_returns(conn):
    print("\n=== Importing Sales & Returns (70K records) ===")

    def tx_return(row):
        return {
            "item_name": row.get("Item Name"),
            "category": row.get("Category"),
            "version": row.get("Version"),
            "item_code": row.get("Item Code"),
            "item_id": row.get("Item ID"),
            "buyer_id": row.get("Buyer ID"),
            "transaction_id": row.get("Transaction ID"),
            "order_date": parse_date(row.get("Date", "")),
            "final_quantity": parse_int(row.get("Final Quantity", "0")),
            "total_revenue": parse_float(row.get("Total Revenue", "0")),
            "price_reductions": parse_float(row.get("Price Reductions", "0")),
            "refunds": parse_float(row.get("Refunds", "0")),
            "final_revenue": parse_float(row.get("Final Revenue", "0")),
            "sales_tax": parse_float(row.get("Sales Tax", "0")),
            "overall_revenue": parse_float(row.get("Overall Revenue", "0")),
            "refunded_item_count": parse_int(row.get("Refunded Item Count", "0")),
            "purchased_item_count": parse_int(row.get("Purchased Item Count", "0")),
        }
    load_csv(
        RETURNS_DIR / "order_dataset.csv",
        "sales_returns",
        ["item_name", "category", "version", "item_code", "item_id",
         "buyer_id", "transaction_id", "order_date", "final_quantity",
         "total_revenue", "price_reductions", "refunds", "final_revenue",
         "sales_tax", "overall_revenue", "refunded_item_count", "purchased_item_count"],
        conn,
        transform=tx_return,
    )


def import_inventory(conn):
    print("\n=== Importing Inventory Daily Snapshots (73K records) ===")

    def tx_inv(row):
        return {
            "snapshot_date": row.get("Date"),
            "store_id": row.get("Store ID"),
            "product_id": row.get("Product ID"),
            "category": row.get("Category"),
            "region": row.get("Region"),
            "inventory_level": parse_int(row.get("Inventory Level", "0")),
            "units_sold": parse_int(row.get("Units Sold", "0")),
            "units_ordered": parse_int(row.get("Units Ordered", "0")),
            "demand_forecast": parse_float(row.get("Demand Forecast", "0")),
            "price": parse_float(row.get("Price", "0")),
            "discount": parse_float(row.get("Discount", "0")),
            "weather": row.get("Weather Condition"),
            "holiday_promo": parse_int(row.get("Holiday/Promotion", "0")),
            "competitor_price": parse_float(row.get("Competitor Pricing", "0")),
            "seasonality": row.get("Seasonality"),
        }
    load_csv(
        INVENTORY_DIR / "retail_store_inventory.csv",
        "inventory_daily",
        ["snapshot_date", "store_id", "product_id", "category", "region",
         "inventory_level", "units_sold", "units_ordered", "demand_forecast",
         "price", "discount", "weather", "holiday_promo", "competitor_price", "seasonality"],
        conn,
        transform=tx_inv,
    )


def build_daily_sales_summary(conn):
    """Aggregate order_items + orders + products into daily_sales_summary."""
    print("\n=== Building daily_sales_summary (derived) ===")
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE daily_sales_summary")
    cur.execute("""
        INSERT INTO daily_sales_summary (sale_date, product_id, category, order_count, item_count, total_revenue, total_freight, avg_review_score)
        SELECT
            DATE(o.order_purchase_timestamp) AS sale_date,
            oi.product_id,
            p.product_category_name AS category,
            COUNT(DISTINCT o.order_id) AS order_count,
            SUM(oi.order_item_id) AS item_count,
            SUM(oi.price) AS total_revenue,
            SUM(oi.freight_value) AS total_freight,
            AVG(r.review_score) AS avg_review_score
        FROM orders o
        JOIN order_items oi ON o.order_id = oi.order_id
        LEFT JOIN products p ON oi.product_id = p.product_id
        LEFT JOIN order_reviews r ON o.order_id = r.order_id
        WHERE o.order_purchase_timestamp IS NOT NULL
        GROUP BY DATE(o.order_purchase_timestamp), oi.product_id, p.product_category_name
    """)
    conn.commit()
    count = cur.rowcount
    cur.close()
    print(f"  [daily_sales_summary] {count:,} rows generated")


def build_seller_performance(conn):
    """Aggregate seller metrics."""
    print("\n=== Building seller_performance (derived) ===")
    cur = conn.cursor()
    cur.execute("TRUNCATE TABLE seller_performance")
    cur.execute("""
        INSERT INTO seller_performance (seller_id, seller_city, seller_state, total_orders, total_items, total_revenue, avg_review_score, avg_delivery_days, late_delivery_pct)
        SELECT
            s.seller_id,
            s.seller_city,
            s.seller_state,
            COUNT(DISTINCT oi.order_id) AS total_orders,
            COUNT(*) AS total_items,
            SUM(oi.price) AS total_revenue,
            AVG(r.review_score) AS avg_review_score,
            AVG(DATEDIFF(o.order_delivered_customer_date, o.order_purchase_timestamp)) AS avg_delivery_days,
            ROUND(100.0 * SUM(CASE WHEN o.order_delivered_customer_date > o.order_estimated_delivery_date THEN 1 ELSE 0 END) / COUNT(*), 2) AS late_delivery_pct
        FROM sellers s
        JOIN order_items oi ON s.seller_id = oi.seller_id
        JOIN orders o ON oi.order_id = o.order_id
        LEFT JOIN order_reviews r ON o.order_id = r.order_id
        WHERE o.order_delivered_customer_date IS NOT NULL
        GROUP BY s.seller_id, s.seller_city, s.seller_state
    """)
    conn.commit()
    count = cur.rowcount
    cur.close()
    print(f"  [seller_performance] {count:,} rows generated")


def print_summary(conn):
    """Print table row counts."""
    print("\n" + "=" * 60)
    print("DATABASE SUMMARY: orderguard_prod")
    print("=" * 60)
    cur = conn.cursor()
    cur.execute("SHOW TABLES")
    tables = [row[0] for row in cur.fetchall()]
    total = 0
    for table in tables:
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        count = cur.fetchone()[0]
        total += count
        print(f"  {table:30s} {count:>10,} rows")
    print(f"  {'TOTAL':30s} {total:>10,} rows")
    print("=" * 60)
    cur.close()


def main():
    # Check data files exist
    if not OLIST_DIR.exists():
        print(f"ERROR: Olist data not found at {OLIST_DIR}")
        print("Run: uv run kaggle datasets download -d olistbr/brazilian-ecommerce --unzip -p data/kaggle/olist/")
        sys.exit(1)

    # 1. Create database
    root_conn = connect_root()
    create_database(root_conn)
    root_conn.close()

    # 2. Connect to database
    conn = connect_db()

    # 3. Create schema
    create_schema(conn)

    # 4. Import data
    import_olist(conn)
    import_returns(conn)
    import_inventory(conn)

    # 5. Build derived tables
    build_daily_sales_summary(conn)
    build_seller_performance(conn)

    # 6. Summary
    print_summary(conn)

    conn.close()
    print("\n[DONE] Production simulation database ready!")
    print(f"  DSN: mysql://{READONLY_USER}:{READONLY_PASS}@{DB_HOST}:3306/{DB_NAME}")


if __name__ == "__main__":
    main()
