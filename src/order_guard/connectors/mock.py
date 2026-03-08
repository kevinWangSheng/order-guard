"""Mock connector with realistic e-commerce data."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from order_guard.connectors.base import BaseConnector

# ---------------------------------------------------------------------------
# Predefined SKU catalog with diverse scenarios
# ---------------------------------------------------------------------------

_PRODUCTS = [
    # Normal products
    {"sku": "SKU-A001", "product_name": "Wireless Earbuds Pro", "category": "Electronics", "warehouse": "US-West", "current_stock": 500, "daily_avg_sales": 20, "return_rate": 0.03, "reorder_lead_time_days": 45, "price": 49.99},
    {"sku": "SKU-A002", "product_name": "Bluetooth Speaker Mini", "category": "Electronics", "warehouse": "US-West", "current_stock": 300, "daily_avg_sales": 15, "return_rate": 0.02, "reorder_lead_time_days": 30, "price": 29.99},
    {"sku": "SKU-B001", "product_name": "Yoga Mat Premium", "category": "Sports", "warehouse": "US-East", "current_stock": 800, "daily_avg_sales": 10, "return_rate": 0.01, "reorder_lead_time_days": 20, "price": 35.00},
    {"sku": "SKU-B002", "product_name": "Resistance Bands Set", "category": "Sports", "warehouse": "US-East", "current_stock": 1200, "daily_avg_sales": 25, "return_rate": 0.015, "reorder_lead_time_days": 15, "price": 19.99},
    # Out of stock — 缺货
    {"sku": "SKU-C001", "product_name": "Phone Case Ultra", "category": "Accessories", "warehouse": "US-West", "current_stock": 5, "daily_avg_sales": 30, "return_rate": 0.04, "reorder_lead_time_days": 60, "price": 15.99},
    # Overstock — 积压
    {"sku": "SKU-D001", "product_name": "Winter Jacket XL", "category": "Apparel", "warehouse": "US-East", "current_stock": 5000, "daily_avg_sales": 2, "return_rate": 0.05, "reorder_lead_time_days": 90, "price": 89.99},
    # High return rate — 高退货率
    {"sku": "SKU-E001", "product_name": "Smart Watch Lite", "category": "Electronics", "warehouse": "EU-Central", "current_stock": 200, "daily_avg_sales": 12, "return_rate": 0.18, "reorder_lead_time_days": 40, "price": 79.99},
    # Slow seller with decent stock
    {"sku": "SKU-F001", "product_name": "Desk Lamp LED", "category": "Home", "warehouse": "US-West", "current_stock": 400, "daily_avg_sales": 3, "return_rate": 0.02, "reorder_lead_time_days": 25, "price": 24.99},
    # New product, zero sales history
    {"sku": "SKU-G001", "product_name": "USB-C Hub 7-in-1", "category": "Electronics", "warehouse": "US-West", "current_stock": 150, "daily_avg_sales": 0, "return_rate": 0.0, "reorder_lead_time_days": 35, "price": 39.99},
]


def _compute_days_of_stock(stock: int, daily_avg: float) -> float | None:
    if daily_avg <= 0:
        return None  # Cannot compute
    return round(stock / daily_avg, 1)


class MockConnector(BaseConnector):
    """Mock data source simulating an e-commerce ERP."""

    name = "mock"
    type = "mock"

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}

    async def health_check(self) -> bool:
        return True

    async def get_inventory(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        results = []
        for p in _PRODUCTS:
            days_of_stock = _compute_days_of_stock(p["current_stock"], p["daily_avg_sales"])
            results.append({
                "sku": p["sku"],
                "product_name": p["product_name"],
                "category": p["category"],
                "warehouse": p["warehouse"],
                "current_stock": p["current_stock"],
                "daily_avg_sales": p["daily_avg_sales"],
                "days_of_stock": days_of_stock,
                "reorder_lead_time_days": p["reorder_lead_time_days"],
                "last_restock_date": (now - timedelta(days=random.randint(5, 60))).strftime("%Y-%m-%d"),
                "snapshot_time": now.isoformat(),
            })
        return results

    async def get_orders(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        date_range = filters.get("date_range", "7d")
        days = int(date_range.replace("d", "")) if isinstance(date_range, str) and date_range.endswith("d") else 7

        now = datetime.now(timezone.utc)
        orders: list[dict[str, Any]] = []
        for p in _PRODUCTS:
            daily = max(1, p["daily_avg_sales"])
            for day_offset in range(days):
                order_date = now - timedelta(days=day_offset)
                qty = max(0, daily + random.randint(-3, 3))
                returned = int(qty * p["return_rate"] * random.uniform(0.5, 1.5))
                orders.append({
                    "order_id": f"ORD-{p['sku']}-{order_date.strftime('%m%d')}-{random.randint(1000,9999)}",
                    "sku": p["sku"],
                    "product_name": p["product_name"],
                    "quantity": qty,
                    "returned_quantity": returned,
                    "return_rate": round(returned / qty, 4) if qty > 0 else 0.0,
                    "revenue": round(qty * p["price"], 2),
                    "order_date": order_date.strftime("%Y-%m-%d"),
                })
        return orders

    async def get_sales(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        date_range = filters.get("date_range", "30d")
        days = int(date_range.replace("d", "")) if isinstance(date_range, str) and date_range.endswith("d") else 30

        now = datetime.now(timezone.utc)
        sales: list[dict[str, Any]] = []
        for p in _PRODUCTS:
            total_qty = 0
            total_revenue = 0.0
            total_returned = 0
            for _ in range(days):
                daily = max(0, p["daily_avg_sales"] + random.randint(-2, 2))
                total_qty += daily
                total_revenue += daily * p["price"]
                total_returned += int(daily * p["return_rate"])

            sales.append({
                "sku": p["sku"],
                "product_name": p["product_name"],
                "category": p["category"],
                "total_quantity": total_qty,
                "total_revenue": round(total_revenue, 2),
                "total_returned": total_returned,
                "return_rate": round(total_returned / total_qty, 4) if total_qty > 0 else 0.0,
                "period_days": days,
                "avg_daily_sales": round(total_qty / days, 1),
            })
        return sales
