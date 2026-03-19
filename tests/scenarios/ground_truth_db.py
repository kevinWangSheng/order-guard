"""Ground truth database for investigation scenario tests.

Creates a controlled SQLite database with deterministic data that matches
the ground_truth sections in scenarios_v2.yaml. Unlike production MySQL/PG,
this data never changes — so ground_truth assertions are always valid.

Data summary (test-warehouse):
  inventory:  5 SKUs (SKU-001 缺货, SKU-003/005 低库存, SKU-002/004 正常)
  orders:     5 orders (last 7 days, ~1008 RMB total)
  returns:    23 returns for SKU-004 (return_rate ~23%)
  sales:      30-day daily sales for all SKUs
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


# ─── Schema + Seed ──────────────────────────────────────────────────────────

def build_ground_truth_db() -> sqlite3.Connection:
    """Build and return an in-memory SQLite DB with all ground truth data."""
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.row_factory = sqlite3.Row
    _create_tables(db)
    _seed_inventory(db)
    _seed_orders(db)
    _seed_returns(db)
    _seed_daily_sales(db)
    db.commit()
    return db


def _create_tables(db: sqlite3.Connection) -> None:
    db.executescript("""
    CREATE TABLE IF NOT EXISTS inventory (
        sku          TEXT PRIMARY KEY,
        name         TEXT NOT NULL,
        quantity     INTEGER NOT NULL,
        safety_stock INTEGER NOT NULL DEFAULT 10,
        warehouse    TEXT NOT NULL DEFAULT '主仓',
        updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS orders (
        id         TEXT PRIMARY KEY,
        sku        TEXT NOT NULL,
        quantity   INTEGER NOT NULL,
        amount     REAL NOT NULL,
        status     TEXT NOT NULL DEFAULT 'pending',
        created_at TEXT NOT NULL,
        FOREIGN KEY (sku) REFERENCES inventory(sku)
    );

    CREATE TABLE IF NOT EXISTS returns (
        id         TEXT PRIMARY KEY,
        order_id   TEXT NOT NULL,
        sku        TEXT NOT NULL,
        quantity   INTEGER NOT NULL,
        reason     TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS daily_sales (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        sku      TEXT NOT NULL,
        sale_day TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        revenue  REAL NOT NULL
    );
    """)


def _seed_inventory(db: sqlite3.Connection) -> None:
    """5 SKUs: 2 缺货/低库存, 2 正常, 1 高退货率."""
    db.executemany(
        "INSERT INTO inventory (sku, name, quantity, safety_stock, warehouse) VALUES (?,?,?,?,?)",
        [
            # SKU-001: 完全缺货 (S01 ground truth)
            ("SKU-001", "无线蓝牙耳机",   0,   50, "主仓"),
            # SKU-002: 正常
            ("SKU-002", "手机保护壳",    200,   30, "主仓"),
            # SKU-003: 低于安全库存 (S01 ground truth)
            ("SKU-003", "USB-C 数据线",    5,   20, "主仓"),
            # SKU-004: 正常库存但高退货率 (S02 ground truth)
            ("SKU-004", "笔记本电脑支架", 100,   15, "华东仓"),
            # SKU-005: 低于安全库存 (S01 ground truth)
            ("SKU-005", "便携充电宝",      3,   25, "华南仓"),
        ],
    )


def _seed_orders(db: sqlite3.Connection) -> None:
    """5 orders within last 7 days. Total ~1008 RMB. (S04 ground truth)."""
    db.executemany(
        "INSERT INTO orders (id, sku, quantity, amount, status, created_at) VALUES (?,?,?,?,?,?)",
        [
            ("ORD-001", "SKU-001", 10,  299.0,  "pending", "2026-03-14 10:00:00"),
            ("ORD-002", "SKU-001",  5,  149.5,  "pending", "2026-03-15 11:00:00"),
            ("ORD-003", "SKU-002",  3,   59.7,  "shipped", "2026-03-16 09:00:00"),
            ("ORD-004", "SKU-003", 20,  180.0,  "pending", "2026-03-17 14:00:00"),
            ("ORD-005", "SKU-005",  8,  320.0,  "pending", "2026-03-18 15:00:00"),
        ],
    )

    # 100 orders for SKU-004 to establish return_rate denominator (S02 ground truth)
    # Use February dates so they don't contaminate the "last 7 days" assertion
    sku4_orders = [
        (f"ORD-S04-{i:03d}", "SKU-004", 1, 89.9, "shipped", f"2026-02-{(i%28)+1:02d} 10:00:00")
        for i in range(100)
    ]
    db.executemany(
        "INSERT INTO orders (id, sku, quantity, amount, status, created_at) VALUES (?,?,?,?,?,?)",
        sku4_orders,
    )


def _seed_returns(db: sqlite3.Connection) -> None:
    """23 returns for SKU-004 → return_rate = 23/100 = 23%. (S02 ground truth)."""
    returns = [
        (
            f"RET-S04-{i:03d}",
            f"ORD-S04-{i:03d}",
            "SKU-004",
            1,
            "产品质量问题" if i % 3 == 0 else ("与描述不符" if i % 3 == 1 else "物流损坏"),
            f"2026-03-{(i%28)+1:02d} 14:00:00",
        )
        for i in range(23)
    ]
    db.executemany(
        "INSERT INTO returns (id, order_id, sku, quantity, reason, created_at) VALUES (?,?,?,?,?,?)",
        returns,
    )

    # 1 return for SKU-002 (normal return rate ~0.5%)
    db.execute(
        "INSERT INTO returns (id, order_id, sku, quantity, reason, created_at) VALUES (?,?,?,?,?,?)",
        ("RET-002-001", "ORD-003", "SKU-002", 1, "买错了", "2026-03-17 09:00:00"),
    )


def _seed_daily_sales(db: sqlite3.Connection) -> None:
    """30 days of daily sales for all SKUs. (S03 monthly overview ground truth)."""
    sales = []
    # SKU-002: 最畅销，每天约 50-80 件
    for day in range(1, 31):
        qty = 50 + (day % 30)  # 50-79 件/天
        sales.append(("SKU-002", f"2026-03-{day:02d}", qty, round(qty * 19.9, 2)))
    # SKU-001: 热卖品，每天约 20-30 件，但库存耗尽
    for day in range(1, 20):  # 只有前19天有货
        qty = 20 + (day % 10)
        sales.append(("SKU-001", f"2026-03-{day:02d}", qty, round(qty * 299.0, 2)))
    # SKU-003/005: 中等
    for day in range(1, 31):
        sales.append(("SKU-003", f"2026-03-{day:02d}", 8, round(8 * 9.0, 2)))
        sales.append(("SKU-005", f"2026-03-{day:02d}", 5, round(5 * 40.0, 2)))
    # SKU-004: 稳定但有退货问题
    for day in range(1, 31):
        sales.append(("SKU-004", f"2026-03-{day:02d}", 3, round(3 * 89.9, 2)))

    db.executemany(
        "INSERT INTO daily_sales (sku, sale_day, quantity, revenue) VALUES (?,?,?,?)",
        sales,
    )


# ─── Assertions helpers ─────────────────────────────────────────────────────

def assert_stockout_skus(db: sqlite3.Connection) -> list[str]:
    """Return SKUs where quantity < safety_stock (for test validation)."""
    rows = db.execute(
        "SELECT sku FROM inventory WHERE quantity < safety_stock ORDER BY quantity"
    ).fetchall()
    return [r["sku"] for r in rows]


def assert_return_rate(db: sqlite3.Connection, sku: str) -> float:
    """Compute return rate for a SKU (returns.count / orders.count)."""
    ret_count = db.execute(
        "SELECT COUNT(*) as c FROM returns WHERE sku=?", (sku,)
    ).fetchone()["c"]
    ord_count = db.execute(
        "SELECT COUNT(*) as c FROM orders WHERE sku=?", (sku,)
    ).fetchone()["c"]
    if ord_count == 0:
        return 0.0
    return round(ret_count / ord_count, 4)


def assert_orders_last_7d(db: sqlite3.Connection) -> dict:
    """Return order stats for the last 7 days (hardcoded cutoff for tests)."""
    rows = db.execute(
        """
        SELECT COUNT(*) as total_orders, SUM(amount) as total_amount
        FROM orders
        WHERE created_at >= '2026-03-13 00:00:00'
          AND status != 'cancelled'
        """
    ).fetchone()
    return {
        "total_orders": rows["total_orders"],
        "total_amount": round(rows["total_amount"] or 0.0, 2),
    }


# ─── Quick self-test ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    db = build_ground_truth_db()

    stockout = assert_stockout_skus(db)
    print(f"Stockout/low-stock SKUs: {stockout}")
    assert set(stockout) == {"SKU-001", "SKU-003", "SKU-005"}, f"Expected 3, got {stockout}"

    sku4_rr = assert_return_rate(db, "SKU-004")
    print(f"SKU-004 return rate: {sku4_rr:.1%}")
    assert 0.20 <= sku4_rr <= 0.26, f"Expected ~23%, got {sku4_rr:.1%}"

    orders = assert_orders_last_7d(db)
    print(f"Orders last 7d: {orders}")
    assert orders["total_orders"] == 5, f"Expected 5, got {orders['total_orders']}"
    assert 900 <= orders["total_amount"] <= 1100, f"Expected ~1008, got {orders['total_amount']}"

    print("✅ All ground truth assertions pass")
