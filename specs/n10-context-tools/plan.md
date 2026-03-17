# N10: 业务知识工具集

## Context
N8 实现了业务知识注入，但更新逻辑耦合在飞书 Bot 的 UPDATE_CONTEXT 意图分支中。v4 改版决定去掉意图分类，业务知识管理改为 3 个 Agent 工具。

同时升级数据模型：加 category（分类）和 expires_at（过期时间），注入时按分类分组，上限 20 条 / 1000 tokens。

## Scope

### In Scope
- 3 个业务知识工具函数：list_context / add_context / delete_context
- 数据模型升级（category enum + expires_at）
- 注入 system prompt 时按分类分组 + 上限控制
- config.yaml 初始业务知识加载
- 统一返回信封 `{data, hint}` / `{error, hint}`

### Not In Scope
- RAG、文档解析、知识图谱
- 外部文档同步（飞书文档/Google Sheet）
- Web UI 管理界面
- Agent 自动识别业务变化保存（system prompt 引导即可，不需要工具支持）

## Design

### 数据模型升级
```python
class BusinessContext(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    content: str                           # 知识内容
    category: str = Field(default="other") # promotion/strategy/supplier/product/logistics/other
    expires_at: datetime | None = None     # 过期时间，None 表示永不过期
    source: str = "chat"                   # config / chat
    created_by: str | None = None          # 用户标识
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

category 枚举：
```python
CONTEXT_CATEGORIES = ["promotion", "strategy", "supplier", "product", "logistics", "other"]
```

### 工具定义

#### list_context
```python
def list_context(category: str | None = None) -> dict:
    """列出当前生效的业务知识"""
    # 过滤掉已过期的（expires_at < now）
    # 可选按 category 筛选
    # 返回：id, content, category, expires_at, source, created_at
    # hint: "共 N 条业务知识。可以添加新的或删除过时的。"
```

Tool Schema:
```json
{
  "name": "list_context",
  "description": "列出当前生效的业务知识（已过期的自动排除）。可按分类筛选。",
  "input_schema": {
    "type": "object",
    "properties": {
      "category": {
        "type": "string",
        "enum": ["promotion", "strategy", "supplier", "product", "logistics", "other"],
        "description": "按分类筛选。不传则返回全部。"
      }
    },
    "required": []
  }
}
```

#### add_context
```python
def add_context(
    content: str,
    category: str = "other",
    expires_at: str | None = None,  # ISO 格式或相对时间如 "7d"
    created_by: str | None = None
) -> dict:
    """添加一条业务知识"""
    # 校验：content 不为空，category 合法
    # 校验：当前总条数 < 20（上限控制）
    # expires_at 支持相对时间解析："7d" → now + 7 days
```

Tool Schema:
```json
{
  "name": "add_context",
  "description": "添加一条业务知识，会注入到后续所有分析中作为背景参考。上限 20 条。",
  "input_schema": {
    "type": "object",
    "properties": {
      "content": {
        "type": "string",
        "description": "业务知识内容，如 '3月全线提价5%' 或 '主要供应商是义乌XX工厂'"
      },
      "category": {
        "type": "string",
        "enum": ["promotion", "strategy", "supplier", "product", "logistics", "other"],
        "description": "知识分类。默认 'other'"
      },
      "expires_at": {
        "type": "string",
        "description": "过期时间。ISO 格式如 '2026-04-01' 或相对时间如 '7d'（7天后过期）、'30d'。不传则永不过期"
      }
    },
    "required": ["content"]
  }
}
```

#### delete_context
```python
def delete_context(context_id: int) -> dict:
    """删除一条业务知识"""
    # config 来源的知识也可以删除（从 DB 标记删除，不改 config 文件）
```

Tool Schema:
```json
{
  "name": "delete_context",
  "description": "删除一条业务知识。删除后不可恢复。",
  "input_schema": {
    "type": "object",
    "properties": {
      "context_id": {
        "type": "integer",
        "description": "业务知识 ID，从 list_context 获取"
      }
    },
    "required": ["context_id"]
  }
}
```

### System Prompt 注入逻辑
```python
def build_context_injection(max_items: int = 20, max_tokens: int = 1000) -> str:
    """构建注入 system prompt 的业务知识文本"""
    # 1. 查询所有未过期的 BusinessContext
    # 2. 按 category 分组
    # 3. 截断到 max_items 条 / max_tokens tokens
    # 4. 格式化输出：
    #
    # ## 业务背景
    # ### 促销活动
    # - 3月全线提价5%
    # - TEMU 平台本周做满减活动
    # ### 供应商
    # - 主要供应商是义乌XX工厂
```

### Config 初始加载
启动时将 config.yaml 中的 `business_context` 文本拆分为多条记录写入 DB（source="config"），避免重复写入（按 source="config" 去重）。

### Key Decisions
- 上限 20 条 / 1000 tokens，超出时提示用户清理旧知识
- expires_at 支持相对时间（"7d"、"30d"），降低用户使用门槛
- 过期知识不删除，只是查询时过滤，用户可以看到（list_context 可加参数显示已过期的）
- delete_context 是写操作，会被 Agent 编排层拦截确认（N12 负责）

## Dependencies
- N8（业务知识注入）— 复用现有 BusinessContext 模型，升级字段
- N9（规则工具集）— 复用 tools 包结构和统一返回信封

## File Changes
- `src/order_guard/tools/context_tools.py` — 3 个工具函数 + Tool Schema
- `src/order_guard/models/tables.py` — BusinessContext 模型升级（加 category, expires_at）
- `alembic/versions/` — DB 迁移
- `tests/test_context_tools.py` — 单元测试

## Tasks
- [ ] N10.1: BusinessContext 模型升级（category enum + expires_at）+ DB 迁移
- [ ] N10.2: 实现 list_context（过滤过期 + 分类筛选 + hint）
- [ ] N10.3: 实现 add_context（校验 + 上限控制 + 相对时间解析 + hint）
- [ ] N10.4: 实现 delete_context + hint
- [ ] N10.5: 实现 build_context_injection（分类分组 + 截断）
- [ ] N10.6: Config 初始加载逻辑（config.yaml → DB）
- [ ] N10.7: 定义 3 个工具的 JSON Schema
- [ ] N10.8: 编写单元测试
