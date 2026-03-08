"""SummaryBuilder — format metrics into Markdown for LLM consumption."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class SummaryBuilder:
    """Build Markdown summary tables from computed metrics."""

    def build_inventory_summary(self, metrics: list[dict[str, Any]]) -> str:
        """Format inventory metrics as a Markdown table."""
        if not metrics:
            return ""

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"库存分析摘要（{now}）",
            "",
            "| SKU | 商品 | 库存 | 日均销量 | 可售天数 | 补货周期 | 风险 |",
            "|-----|------|------|---------|---------|---------|------|",
        ]
        for m in metrics:
            days = m.get("days_of_stock")
            days_str = f"{days}天" if days is not None else "N/A"
            lines.append(
                f"| {m['sku']} | {m['product_name']} | {m['current_stock']} "
                f"| {m['daily_avg_sales']} | {days_str} "
                f"| {m['reorder_lead_time_days']}天 | {m['stock_risk']} |"
            )
        return "\n".join(lines)

    def build_order_summary(self, metrics: list[dict[str, Any]]) -> str:
        """Format order metrics as a Markdown table."""
        if not metrics:
            return ""

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [
            f"订单分析摘要（{now}）",
            "",
            "| SKU | 商品 | 订单数 | 总数量 | 退货数 | 退货率 | 总营收 |",
            "|-----|------|--------|--------|--------|--------|--------|",
        ]
        for m in metrics:
            rate_pct = f"{m['return_rate'] * 100:.1f}%"
            lines.append(
                f"| {m['sku']} | {m['product_name']} | {m['total_orders']} "
                f"| {m['total_quantity']} | {m['total_returned']} | {rate_pct} "
                f"| ${m['total_revenue']:,.2f} |"
            )
        return "\n".join(lines)

    def build(self, inventory_metrics: list[dict[str, Any]] | None = None,
              order_metrics: list[dict[str, Any]] | None = None) -> str:
        """Build combined summary from all available metrics."""
        parts = []
        if inventory_metrics:
            parts.append(self.build_inventory_summary(inventory_metrics))
        if order_metrics:
            parts.append(self.build_order_summary(order_metrics))
        return "\n\n".join(parts)
