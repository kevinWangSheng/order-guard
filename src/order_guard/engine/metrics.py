"""Metrics computation engine — code computes, LLM judges."""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Stock risk classification
# ---------------------------------------------------------------------------

def compute_days_of_stock(current_stock: int | float, daily_avg_sales: float) -> float | None:
    """Calculate how many days current stock can last."""
    if daily_avg_sales <= 0:
        return None
    return round(current_stock / daily_avg_sales, 1)


def classify_stock_risk(
    days_of_stock: float | None,
    reorder_lead_time: int,
) -> str:
    """Classify stock risk based on days of stock vs lead time.

    Returns: "缺货风险" | "积压风险" | "正常"
    """
    if days_of_stock is None:
        return "无销量数据"
    if days_of_stock < reorder_lead_time:
        return "缺货风险"
    if days_of_stock > reorder_lead_time * 5:
        return "积压风险"
    return "正常"


# ---------------------------------------------------------------------------
# Order / return metrics
# ---------------------------------------------------------------------------

def compute_return_rate(total_returned: int, total_orders: int) -> float:
    """Compute return rate as a fraction."""
    if total_orders <= 0:
        return 0.0
    return round(total_returned / total_orders, 4)


# ---------------------------------------------------------------------------
# MetricsEngine — orchestrates computation over raw data
# ---------------------------------------------------------------------------

class MetricsEngine:
    """Compute metrics from raw connector data."""

    def compute_inventory_metrics(self, inventory_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Enrich inventory data with computed metrics."""
        results = []
        for item in inventory_data:
            stock = item.get("current_stock", 0)
            daily_avg = item.get("daily_avg_sales", 0)
            lead_time = item.get("reorder_lead_time_days", 30)

            days = compute_days_of_stock(stock, daily_avg)
            risk = classify_stock_risk(days, lead_time)

            results.append({
                "sku": item.get("sku", ""),
                "product_name": item.get("product_name", ""),
                "category": item.get("category", ""),
                "warehouse": item.get("warehouse", ""),
                "current_stock": stock,
                "daily_avg_sales": daily_avg,
                "days_of_stock": days,
                "reorder_lead_time_days": lead_time,
                "stock_risk": risk,
            })
        return results

    def compute_order_metrics(self, orders_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aggregate orders by SKU and compute per-SKU metrics."""
        sku_agg: dict[str, dict[str, Any]] = {}
        for order in orders_data:
            sku = order.get("sku", "")
            if sku not in sku_agg:
                sku_agg[sku] = {
                    "sku": sku,
                    "product_name": order.get("product_name", ""),
                    "total_orders": 0,
                    "total_quantity": 0,
                    "total_returned": 0,
                    "total_revenue": 0.0,
                }
            agg = sku_agg[sku]
            agg["total_orders"] += 1
            agg["total_quantity"] += order.get("quantity", 0)
            agg["total_returned"] += order.get("returned_quantity", 0)
            agg["total_revenue"] += order.get("revenue", 0.0)

        results = []
        for agg in sku_agg.values():
            agg["return_rate"] = compute_return_rate(agg["total_returned"], agg["total_quantity"])
            agg["total_revenue"] = round(agg["total_revenue"], 2)
            results.append(agg)
        return results
