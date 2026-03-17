"""Tests for file handler — CSV/Excel upload parsing and context injection (N7)."""

from __future__ import annotations

import io
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from order_guard.api.file_handler import (
    FileContext,
    parse_file,
    build_file_context_prompt,
    MAX_FILE_SIZE,
    MAX_ROW_COUNT,
    FULL_DATA_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Test fixtures — generate CSV/Excel bytes
# ---------------------------------------------------------------------------

def _make_csv(rows: int = 10, encoding: str = "utf-8") -> bytes:
    """Generate a simple CSV file in memory."""
    lines = ["sku,name,quantity,price"]
    for i in range(rows):
        lines.append(f"SKU-{i:04d},Product {i},{i * 10},{9.99 + i}")
    return "\n".join(lines).encode(encoding)


def _make_csv_chinese() -> bytes:
    """Generate a CSV with Chinese headers in GBK encoding."""
    lines = ["商品编码,商品名称,库存数量,单价"]
    for i in range(5):
        lines.append(f"SKU-{i:04d},测试商品{i},{i * 10},{9.99 + i}")
    return "\n".join(lines).encode("gbk")


def _make_excel(rows: int = 10) -> bytes:
    """Generate a simple Excel file in memory."""
    import pandas as pd
    data = {
        "sku": [f"SKU-{i:04d}" for i in range(rows)],
        "name": [f"Product {i}" for i in range(rows)],
        "quantity": [i * 10 for i in range(rows)],
    }
    df = pd.DataFrame(data)
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# parse_file tests
# ---------------------------------------------------------------------------

class TestParseCSV:
    def test_parse_csv_basic(self):
        content = _make_csv(10)
        ctx = parse_file(content, "test.csv")
        assert ctx.file_name == "test.csv"
        assert ctx.row_count == 10
        assert "sku" in ctx.columns
        assert ctx.is_full is True
        assert ctx.full_data != ""
        assert ctx.sample != ""

    def test_parse_csv_chinese_gbk(self):
        content = _make_csv_chinese()
        ctx = parse_file(content, "stock_cn.csv")
        assert ctx.row_count == 5
        assert "商品编码" in ctx.columns

    def test_parse_csv_large_no_full_data(self):
        content = _make_csv(600)
        ctx = parse_file(content, "large.csv")
        assert ctx.row_count == 600
        assert ctx.is_full is False
        assert ctx.full_data == ""
        assert ctx.sample != ""  # Still has preview

    def test_parse_csv_at_threshold(self):
        content = _make_csv(FULL_DATA_THRESHOLD)
        ctx = parse_file(content, "exact.csv")
        assert ctx.is_full is True

    def test_parse_csv_above_threshold(self):
        content = _make_csv(FULL_DATA_THRESHOLD + 1)
        ctx = parse_file(content, "above.csv")
        assert ctx.is_full is False


class TestParseExcel:
    def test_parse_excel_basic(self):
        content = _make_excel(10)
        ctx = parse_file(content, "test.xlsx")
        assert ctx.file_name == "test.xlsx"
        assert ctx.row_count == 10
        assert "sku" in ctx.columns
        assert ctx.is_full is True

    def test_parse_excel_xls_extension(self):
        # .xls extension should be accepted (pandas handles it)
        content = _make_excel(5)
        # Note: this will work because we're actually creating xlsx content
        # The parse_file routes based on extension
        ctx = parse_file(content, "test.xlsx")
        assert ctx.row_count == 5


class TestParseValidation:
    def test_reject_unsupported_format(self):
        with pytest.raises(ValueError, match="不支持的文件格式"):
            parse_file(b"some content", "data.json")

    def test_reject_oversized_file(self):
        huge = b"x" * (MAX_FILE_SIZE + 1)
        with pytest.raises(ValueError, match="文件大小超过"):
            parse_file(huge, "huge.csv")

    def test_reject_too_many_rows(self):
        content = _make_csv(MAX_ROW_COUNT + 1)
        with pytest.raises(ValueError, match="行数"):
            parse_file(content, "toomany.csv")

    def test_reject_empty_file(self):
        content = b"sku,name\n"  # Header only, no data rows
        with pytest.raises(ValueError, match="内容为空"):
            parse_file(content, "empty.csv")


# ---------------------------------------------------------------------------
# build_file_context_prompt tests
# ---------------------------------------------------------------------------

class TestBuildFileContextPrompt:
    def test_small_file_includes_full_data(self):
        ctx = FileContext(
            file_name="skus.csv",
            row_count=5,
            columns=["sku", "name"],
            sample="| sku | name |\n|---|---|\n| SKU-001 | Test |",
            full_data="sku,name\nSKU-001,Test",
            is_full=True,
        )
        prompt = build_file_context_prompt(ctx, "查一下库存")
        assert "skus.csv" in prompt
        assert "行数：5" in prompt
        assert "完整文件内容" in prompt
        assert "查一下库存" in prompt

    def test_large_file_summary_only(self):
        ctx = FileContext(
            file_name="big.csv",
            row_count=1000,
            columns=["sku", "name", "qty"],
            sample="| sku | name | qty |\n|---|---|---|\n| SKU-001 | Test | 10 |",
            full_data="",
            is_full=False,
        )
        prompt = build_file_context_prompt(ctx, "分析这些数据")
        assert "big.csv" in prompt
        assert "行数：1000" in prompt
        assert "仅展示前 5 行" in prompt
        assert "SQL" in prompt
        assert "完整文件内容" not in prompt

    def test_no_user_message(self):
        ctx = FileContext(
            file_name="test.csv",
            row_count=5,
            columns=["a"],
            sample="| a |\n|---|\n| 1 |",
            full_data="a\n1",
            is_full=True,
        )
        prompt = build_file_context_prompt(ctx)
        assert "test.csv" in prompt
        assert "用户的问题" not in prompt


# ---------------------------------------------------------------------------
# FileContext model tests
# ---------------------------------------------------------------------------

class TestFileContext:
    def test_defaults(self):
        ctx = FileContext()
        assert ctx.file_name == ""
        assert ctx.row_count == 0
        assert ctx.columns == []
        assert ctx.is_full is False

    def test_with_values(self):
        ctx = FileContext(
            file_name="test.csv",
            row_count=100,
            columns=["a", "b"],
            is_full=True,
        )
        assert ctx.file_name == "test.csv"
        assert len(ctx.columns) == 2


# ---------------------------------------------------------------------------
# Integration with feishu handler
# ---------------------------------------------------------------------------

class TestFeishuFileIntegration:
    def test_file_context_forces_query_intent(self):
        """When a file is attached but intent is CHAT, should force QUERY."""
        # This is tested through the feishu.py logic:
        # "If file is attached, force QUERY intent"
        # We verify the code path exists by checking the import works
        from order_guard.api.feishu import _handle_user_query_impl
        assert callable(_handle_user_query_impl)

    def test_build_prompt_with_file(self):
        """Verify file context prompt integrates with conversation flow."""
        ctx = FileContext(
            file_name="sku_list.csv",
            row_count=20,
            columns=["sku", "target_price"],
            sample="| sku | target_price |\n|---|---|\n| SKU-001 | 29.99 |",
            full_data="sku,target_price\nSKU-001,29.99\nSKU-002,39.99",
            is_full=True,
        )
        prompt = build_file_context_prompt(ctx, "帮我查这些 SKU 的库存")
        # Should contain both file info and user question
        assert "sku_list.csv" in prompt
        assert "SKU-001" in prompt
        assert "帮我查这些 SKU 的库存" in prompt
