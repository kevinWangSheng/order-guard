# N7: CSV 辅助输入

## Context
用户有时需要基于一份 SKU 清单或对比数据做分析，比如"这 200 个 SKU 的库存情况""对比这份竞品价格表"。CSV 不是独立数据源，而是对话中的辅助输入，结合已接入的数据库做分析。

## Scope

### In Scope
- 飞书 Bot 支持接收文件附件（CSV / Excel）
- 文件解析为结构化数据
- 数据作为 context 注入当前对话
- Agent 结合 CSV 数据 + 数据库数据进行分析
- 文件大小和行数限制

### Not In Scope
- CSV 作为独立数据源接入
- CSV 数据持久化到 DB
- 文件格式转换
- 大文件处理（>10MB）

## Design

### 用户交互流程
```
用户在飞书发送一个 CSV 文件 + 消息：
"这是我们要清仓的 SKU 清单，帮我查一下它们的库存和近期销量"

系统：
1. 检测到文件附件 → 下载 CSV
2. 解析 CSV（pandas）
3. 将 CSV 内容摘要注入对话 context
4. Agent 查数据库，结合 CSV 数据分析
5. 回复分析结果
```

### 文件处理
```python
async def process_uploaded_file(file_url: str, file_name: str) -> FileContext:
    """下载并解析上传的文件"""
    # 1. 下载文件（飞书 API）
    content = await download_feishu_file(file_url)

    # 2. 解析
    if file_name.endswith('.csv'):
        df = pd.read_csv(io.BytesIO(content))
    elif file_name.endswith('.xlsx'):
        df = pd.read_excel(io.BytesIO(content))
    else:
        raise ValueError(f"不支持的文件格式: {file_name}")

    # 3. 限制
    if len(df) > 5000:
        raise ValueError("文件行数超过 5000 行限制")
    if len(content) > 10 * 1024 * 1024:
        raise ValueError("文件大小超过 10MB 限制")

    # 4. 生成摘要
    return FileContext(
        file_name=file_name,
        row_count=len(df),
        columns=list(df.columns),
        sample=df.head(5).to_markdown(),
        full_data=df.to_csv(index=False),  # 完整数据（如果不太大）
    )
```

### Context 注入
```python
# CSV 数据注入到 Agent 的 user message 中
user_prompt = f"""
用户上传了文件：{file_context.file_name}
行数：{file_context.row_count}
列：{file_context.columns}

文件内容（前 5 行预览）：
{file_context.sample}

{file_context.full_data if file_context.row_count <= 500 else "（文件较大，请通过 SQL 查询数据库中的对应数据）"}

用户的问题：{user_message}
"""
```

如果 CSV 行数 <= 500，全量注入 context（LLM 直接分析）。
如果 CSV 行数 > 500，只注入列名和样本，提示 Agent 用 SQL 关联查询。

### 飞书文件下载
```python
async def download_feishu_file(message_id: str, file_key: str) -> bytes:
    """通过飞书 API 下载文件附件"""
    # GET /open-apis/im/v1/messages/{message_id}/resources/{file_key}
    ...
```

### Key Decisions
- CSV 数据不持久化，只在当前会话中有效
- 行数 <= 500 全量注入 context；> 500 只注入摘要
- 支持 CSV 和 Excel 两种格式
- 文件大小限制 10MB，行数限制 5000
- 编码自动检测（复用 chardet）

## Dependencies
- N5（会话管理）— 文件 context 跟随当前会话
- N1（统一数据访问层）— Agent 查数据库关联分析

## File Changes
- `src/order_guard/api/file_handler.py` — 文件下载、解析、context 生成（新文件）
- `src/order_guard/api/feishu.py` — 文件消息处理 + context 注入
- `tests/test_file_handler.py` — 单元测试
- `tests/fixtures/` — 测试用 CSV 文件

## Tasks
- [ ] N7.1: 文件下载（飞书 API）+ 解析（CSV / Excel）
- [ ] N7.2: FileContext 生成（摘要 + 全量数据判断）
- [ ] N7.3: 飞书 Bot 适配文件消息 + context 注入
- [ ] N7.4: 编写单元测试
