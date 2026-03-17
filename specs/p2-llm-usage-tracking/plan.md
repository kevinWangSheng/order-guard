# P2: LLM 用量追踪

## Context
Agent 每次调用 LLM 都有 `TokenUsage`（prompt_tokens / completion_tokens / total_tokens）返回，但只在日志中打印，没有持久化。用户无法知道：
- 这个月花了多少 token / 多少钱
- 哪条规则最费 token
- 对话查询 vs 定时巡检 vs 报告生成的成本比例

企业客户对 LLM 成本非常敏感，用量不可见 = 成本不可控。

## Scope

### In Scope
- `LLMUsageLog` 新表：记录每次 LLM 调用的 token 用量
- Agent / Scheduler / Reporter 自动记录（非侵入式）
- 1 个新 Agent 工具：`get_usage_stats`
- 内置模型价格表（主流模型，可配置覆盖）
- 对话场景覆盖：查总量、按规则查、按时间查、成本估算

### Not In Scope
- LLM 成本预算/限额（超额自动停止）
- 实时计费 Dashboard（Web UI）
- 按用户计费

## Design

### 数据模型

```python
class LLMUsageLog(SQLModel, table=True):
    __tablename__ = "llm_usage_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    model: str = ""                    # 模型名称，如 "qwen/qwen3-coder-plus"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_estimate_usd: float = 0.0     # 估算成本（美元）
    trigger_type: str = ""             # "chat" / "rule" / "report"
    rule_id: str = ""                  # 规则触发时关联的 rule_id
    user_id: str = ""                  # 对话触发时关联的 user_id
    session_id: str = ""               # 对话触发时关联的 session_id
    duration_ms: int = 0               # LLM 调用耗时
    tool_calls_count: int = 0          # 该次 Agent 运行的工具调用次数
    iterations: int = 0                # Agent 循环次数
    created_at: datetime = Field(default_factory=_utcnow)
```

### 成本估算

内置价格表（每百万 token，美元）：
```python
MODEL_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "qwen/qwen3-coder-plus": {"input": 0.50, "output": 2.00},
    # 可通过配置覆盖
}
```

价格匹配逻辑：精确匹配 → 前缀匹配（如 `gpt-4o-2024` 匹配 `gpt-4o`）→ 未知模型标记 cost = 0。

settings.py 新增：
```python
class LLMConfig(BaseModel):
    # ... 现有字段 ...
    custom_pricing: dict[str, dict[str, float]] = {}  # 用户自定义价格覆盖
```

### 记录切入点

在 Agent 运行结束时统一记录（不是每次 LLM 调用都记，而是每次 Agent 完整运行记一条）：

```python
# agent.py — run() / run_unified() 结束时
async def _log_usage(
    token_usage: TokenUsage,
    model: str,
    trigger_type: str,
    rule_id: str = "",
    user_id: str = "",
    session_id: str = "",
    duration_ms: int = 0,
    tool_calls_count: int = 0,
    iterations: int = 0,
):
    """持久化 LLM 用量记录"""
```

触发场景：
1. **rule 巡检**：`scheduler/jobs.py` → `agent.run()` → trigger_type="rule", rule_id=xxx
2. **对话查询**：`feishu.py` → `agent.run_unified()` → trigger_type="chat", user_id=xxx, session_id=xxx
3. **报告生成**：`reporter.py` → `agent.run()` → trigger_type="report", rule_id=report_id

### 工具定义

#### get_usage_stats
```python
async def get_usage_stats(
    time_range: str = "7d",
    group_by: str | None = None,       # "rule" / "trigger_type" / "model" / "day"
    rule_id: str | None = None
) -> dict:
    """查询 LLM 用量统计"""
    # 返回：
    # total_tokens, total_cost_usd
    # by_group（按 group_by 分组的明细）
    # daily_trend（最近 N 天的每日用量）
```

Tool Schema:
```json
{
  "name": "get_usage_stats",
  "description": "查询 LLM 用量和成本统计。可按时间范围查看总量，按规则/触发类型/模型/天分组查看明细。用于了解 AI 使用成本。",
  "input_schema": {
    "type": "object",
    "properties": {
      "time_range": {
        "type": "string",
        "enum": ["24h", "7d", "30d"],
        "description": "统计时间范围，默认 7d"
      },
      "group_by": {
        "type": "string",
        "enum": ["rule", "trigger_type", "model", "day"],
        "description": "分组维度。rule=按规则，trigger_type=按触发类型，model=按模型，day=按天"
      },
      "rule_id": {
        "type": "string",
        "description": "按规则筛选。不传则统计全部"
      }
    },
    "required": []
  }
}
```

### 对话场景示例
```
用户：这个月LLM花了多少钱
Agent：调用 get_usage_stats(time_range="30d")
Agent：最近 30 天共消耗 125,000 tokens，估算成本 $0.35。其中定时巡检占 60%，对话查询占 30%，报告生成占 10%。

用户：哪条规则最费 token
Agent：调用 get_usage_stats(time_range="30d", group_by="rule")
Agent：消耗最多的是"缺货检测"规则（45,000 tokens），主要因为每次需要查询 18 张表。建议考虑缩小数据范围。
```

## Dependencies
- N12（统一 Agent）— 新工具注册
- Engine Agent — token_usage 数据来源

## File Changes
- `src/order_guard/models/tables.py` — 新增 LLMUsageLog 模型
- `src/order_guard/tools/usage_tools.py` — 新建，get_usage_stats 工具
- `src/order_guard/engine/agent.py` — 运行结束时调用 _log_usage
- `src/order_guard/scheduler/jobs.py` — 传递 trigger_type="rule"
- `src/order_guard/api/feishu.py` — 传递 trigger_type="chat"
- `src/order_guard/engine/reporter.py` — 传递 trigger_type="report"
- `src/order_guard/config/settings.py` — LLMConfig 新增 custom_pricing
- `alembic/versions/xxx_add_llm_usage_logs.py` — 迁移脚本
- `tests/test_usage_tools.py` — 单元测试

## Tasks
- [ ] P2.1: LLMUsageLog 模型 + Alembic 迁移
- [ ] P2.2: 内置模型价格表 + 成本估算函数
- [ ] P2.3: settings 新增 custom_pricing 配置
- [ ] P2.4: Agent _log_usage 函数实现
- [ ] P2.5: scheduler/feishu/reporter 传递 trigger_type 和关联 ID
- [ ] P2.6: 实现 get_usage_stats 工具（聚合 + 分组 + 格式化）
- [ ] P2.7: 注册到统一 Agent 工具集
- [ ] P2.8: 编写单元测试
- [ ] P2.9: 全量回归测试
