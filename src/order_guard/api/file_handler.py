"""File handler — parse CSV/Excel uploads and generate context for Agent."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger

# Limits
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_ROW_COUNT = 5000
FULL_DATA_THRESHOLD = 500  # Rows; above this, only inject summary


@dataclass
class FileContext:
    """Parsed file context for injection into Agent conversation."""

    file_name: str = ""
    row_count: int = 0
    columns: list[str] = field(default_factory=list)
    sample: str = ""          # First 5 rows as markdown table
    full_data: str = ""       # Full CSV text (empty if too large)
    is_full: bool = False     # Whether full_data contains all rows


async def download_feishu_file(
    message_id: str,
    file_key: str,
    app_id: str,
    app_secret: str,
) -> bytes:
    """Download a file attachment from Feishu API."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get tenant token
        token_resp = await client.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
        )
        token = token_resp.json().get("tenant_access_token", "")
        if not token:
            raise ValueError("Failed to get Feishu access token")

        # Download file
        resp = await client.get(
            f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}",
            headers={"Authorization": f"Bearer {token}"},
            params={"type": "file"},
        )
        if resp.status_code != 200:
            raise ValueError(f"File download failed: HTTP {resp.status_code}")
        return resp.content


def parse_file(content: bytes, file_name: str) -> FileContext:
    """Parse CSV or Excel file bytes into FileContext.

    Raises ValueError for unsupported formats, oversized files, or too many rows.
    """
    # Check file size
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"文件大小超过 {MAX_FILE_SIZE // (1024*1024)}MB 限制")

    # Determine format
    lower_name = file_name.lower()
    if lower_name.endswith(".csv"):
        df = _parse_csv(content)
    elif lower_name.endswith((".xlsx", ".xls")):
        df = _parse_excel(content)
    else:
        raise ValueError(f"不支持的文件格式: {file_name}。支持 CSV 和 Excel (.xlsx) 格式。")

    # Check row count
    if len(df) > MAX_ROW_COUNT:
        raise ValueError(f"文件行数 ({len(df)}) 超过 {MAX_ROW_COUNT} 行限制")

    if len(df) == 0:
        raise ValueError("文件内容为空")

    # Generate sample (first 5 rows as markdown)
    sample = df.head(5).to_markdown(index=False)

    # Full data for small files
    is_full = len(df) <= FULL_DATA_THRESHOLD
    full_data = df.to_csv(index=False) if is_full else ""

    return FileContext(
        file_name=file_name,
        row_count=len(df),
        columns=list(df.columns),
        sample=sample,
        full_data=full_data,
        is_full=is_full,
    )


def build_file_context_prompt(file_ctx: FileContext, user_message: str = "") -> str:
    """Build a prompt string that includes the file context."""
    parts = [
        f"用户上传了文件：{file_ctx.file_name}",
        f"行数：{file_ctx.row_count}",
        f"列：{', '.join(file_ctx.columns)}",
        "",
        "文件内容预览（前 5 行）：",
        file_ctx.sample,
    ]

    if file_ctx.is_full and file_ctx.full_data:
        parts.extend(["", "完整文件内容：", file_ctx.full_data])
    elif not file_ctx.is_full:
        parts.extend([
            "",
            f"（文件共 {file_ctx.row_count} 行，仅展示前 5 行。"
            "请结合文件列名，通过 SQL 查询数据库中的对应数据。）",
        ])

    if user_message:
        parts.extend(["", f"用户的问题：{user_message}"])

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal parsers
# ---------------------------------------------------------------------------

def _parse_csv(content: bytes) -> Any:
    """Parse CSV bytes with encoding detection."""
    import pandas as pd

    # Try to detect encoding
    encoding = _detect_encoding(content)
    try:
        return pd.read_csv(io.BytesIO(content), encoding=encoding)
    except Exception:
        # Fallback to utf-8
        try:
            return pd.read_csv(io.BytesIO(content), encoding="utf-8")
        except Exception:
            return pd.read_csv(io.BytesIO(content), encoding="latin-1")


def _parse_excel(content: bytes) -> Any:
    """Parse Excel bytes."""
    import pandas as pd
    return pd.read_excel(io.BytesIO(content))


def _detect_encoding(content: bytes) -> str:
    """Detect file encoding using chardet."""
    try:
        import chardet
        result = chardet.detect(content[:10000])
        encoding = result.get("encoding", "utf-8")
        return encoding or "utf-8"
    except ImportError:
        return "utf-8"
