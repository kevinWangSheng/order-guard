"""Create test SQLite database with simulated warehouse data for MCP e2e testing."""

import sqlite3
from pathlib import Path


DB_PATH = Path("data/test_warehouse.db")


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            sku TEXT PRIMARY KEY,
            name TEXT,
            category TEXT,
            unit_cost REAL,
            unit_price REAL
        );

        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT,
            warehouse TEXT,
            quantity INTEGER,
            reorder_point INTEGER,
            lead_time_days INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT,
            sku TEXT,
            quantity INTEGER,
            status TEXT,
            order_date TEXT,
            delivery_date TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_sales (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT,
            sale_date TEXT,
            quantity_sold INTEGER,
            revenue REAL
        );
    """)


def insert_products(conn: sqlite3.Connection) -> None:
    products = [
        ("SKU-001", "无线蓝牙耳机", "电子产品", 45.0, 129.0),
        ("SKU-002", "手机保护壳", "手机配件", 5.0, 39.0),
        ("SKU-003", "USB-C 数据线", "电子配件", 3.0, 19.0),
        ("SKU-004", "蓝牙音箱", "电子产品", 80.0, 299.0),
        ("SKU-005", "智能手环", "穿戴设备", 60.0, 199.0),
    ]
    conn.executemany("INSERT OR REPLACE INTO products VALUES (?, ?, ?, ?, ?)", products)


def insert_inventory(conn: sqlite3.Connection) -> None:
    inventory = [
        # SKU-001: 充足库存，正常 → info
        ("SKU-001", "华东仓", 500, 100, 7),
        # SKU-002: 库存极低，缺货风险 → critical
        ("SKU-002", "华东仓", 15, 200, 5),
        # SKU-003: 大量积压，销量极低 → warning (积压)
        ("SKU-003", "华东仓", 5000, 50, 7),
        # SKU-004: 退货率高（在 orders 中体现）
        ("SKU-004", "华东仓", 200, 80, 10),
        # SKU-005: 销量骤增，库存即将不足 → warning
        ("SKU-005", "华东仓", 80, 60, 7),
    ]
    conn.executemany(
        "INSERT INTO inventory (sku, warehouse, quantity, reorder_point, lead_time_days) VALUES (?, ?, ?, ?, ?)",
        inventory,
    )


def insert_daily_sales(conn: sqlite3.Connection) -> None:
    """Insert 30 days of sales data."""
    import random
    random.seed(42)

    sales_patterns = {
        # SKU-001: stable ~15/day
        "SKU-001": lambda: random.randint(10, 20),
        # SKU-002: stable ~40/day (high demand, low stock → critical)
        "SKU-002": lambda: random.randint(30, 50),
        # SKU-003: very low ~1/day (5000 stock → 积压)
        "SKU-003": lambda: random.randint(0, 2),
        # SKU-004: moderate ~10/day
        "SKU-004": lambda: random.randint(5, 15),
        # SKU-005: surging from ~5 to ~20/day recently
        "SKU-005": lambda day: random.randint(3, 8) if day < 20 else random.randint(15, 25),
    }

    rows = []
    for day in range(30):
        date = f"2026-02-{day + 1:02d}"
        for sku, pattern in sales_patterns.items():
            try:
                qty = pattern(day)
            except TypeError:
                qty = pattern()
            price_map = {"SKU-001": 129.0, "SKU-002": 39.0, "SKU-003": 19.0, "SKU-004": 299.0, "SKU-005": 199.0}
            revenue = qty * price_map[sku]
            rows.append((sku, date, qty, revenue))

    conn.executemany(
        "INSERT INTO daily_sales (sku, sale_date, quantity_sold, revenue) VALUES (?, ?, ?, ?)",
        rows,
    )


def insert_orders(conn: sqlite3.Connection) -> None:
    """Insert order data with returns."""
    import random
    random.seed(123)

    statuses_normal = ["delivered"] * 9 + ["shipped"]  # ~0% return rate
    statuses_high_return = ["delivered"] * 5 + ["returned"] * 3 + ["shipped", "cancelled"]  # ~30% return rate

    rows = []
    order_counter = 1000

    for day in range(30):
        date = f"2026-02-{day + 1:02d}"
        delivery = f"2026-02-{min(day + 3, 28):02d}"

        for sku in ["SKU-001", "SKU-002", "SKU-003", "SKU-005"]:
            for _ in range(random.randint(2, 5)):
                order_counter += 1
                status = random.choice(statuses_normal)
                qty = random.randint(1, 3)
                rows.append((f"ORD-{order_counter}", sku, qty, status, date, delivery))

        # SKU-004: high return rate
        for _ in range(random.randint(3, 8)):
            order_counter += 1
            status = random.choice(statuses_high_return)
            qty = random.randint(1, 3)
            rows.append((f"ORD-{order_counter}", "SKU-004", qty, status, date, delivery))

    conn.executemany(
        "INSERT INTO orders (order_id, sku, quantity, status, order_date, delivery_date) VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Remove existing DB
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(str(DB_PATH))
    try:
        create_tables(conn)
        insert_products(conn)
        insert_inventory(conn)
        insert_daily_sales(conn)
        insert_orders(conn)
        conn.commit()

        # Verify
        cursor = conn.cursor()
        for table in ["products", "inventory", "daily_sales", "orders"]:
            count = cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count} rows")

        print(f"\nDatabase created: {DB_PATH}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
