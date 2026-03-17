# T22: 热冷数据 + 查询优化

## Context
真实数据库可能有几百万行数据。AI Agent 如果不加约束，可能全表扫描导致超时或拉出海量数据浪费 token。需要：
1. 规则级别配置时间窗口，AI 自动加时间约束
2. 大表自动分步查询策略
3. 查询结果缓存，避免重复查库

## Scope

### In Scope
- 规则级别时间窗口配置（data_window）
- Agent prompt 自动注入时间约束
- 大表分步查询策略（先 COUNT → 采样 → 精确查询）
- 冷数据表标记（归档表不查）
- 查询结果短期缓存（可选）

### Not In Scope
- 数据库层面的分区/归档管理（由 DBA 负责）
- 数据同步/ETL

## Design

### 时间窗口配置
```yaml
rules:
  - id: "rule-inventory-check"
    mcp_server: "production-erp"
    data_window: "7d"              # 只查最近 7 天
    prompt: |
      检查库存情况...

  - id: "rule-trend-analysis"
    mcp_server: "production-erp"
    data_window: "90d"             # 趋势分析查 90 天
    prompt: |
      分析销售趋势...
```

### Agent Prompt 自动注入时间约束
```python
def build_time_constraint(data_window: str) -> str:
    """根据时间窗口生成 Agent 提示"""
    return f"""
重要约束：
- 你只需要分析最近 {data_window} 的数据
- 所有 SQL 查询的 WHERE 条件必须包含时间过滤
- 时间字段常见名称：created_at, order_date, sale_date, updated_at
- 示例：WHERE order_date >= DATE_SUB(NOW(), INTERVAL {data_window})
- 不要查询超出此时间范围的数据
"""
```

### 大表分步查询策略
```
Agent system prompt 增加:

查询策略：
1. 对任何表，先执行 SELECT COUNT(*) 了解数据量
2. 如果数据量 > 10000 行：
   - 先 LIMIT 100 采样了解数据特征
   - 使用 WHERE 条件精确过滤，避免全表扫描
   - 使用 GROUP BY 聚合而非拉明细
3. 如果数据量 < 10000 行：可以直接查询
4. 永远不要 SELECT * 不加 WHERE 和 LIMIT
```

### 冷数据表标记
```yaml
mcp_servers:
  - name: "production-erp"
    type: "dbhub"
    schema_filter:
      cold_tables: ["orders_archive_2024", "sales_history_2023"]
      # 冷数据表在 schema context 中标记为"归档表，仅在明确需要历史数据时查询"
```

### 查询缓存（可选）
```python
class QueryCache:
    """短期缓存，避免同一 Agent 循环内重复查询"""

    def __init__(self, ttl_seconds: int = 300):
        self._cache: dict[str, CacheEntry] = {}
        self._ttl = ttl_seconds

    def get(self, sql: str) -> Any | None:
        entry = self._cache.get(sql)
        if entry and not entry.expired:
            return entry.result
        return None

    def set(self, sql: str, result: Any) -> None:
        self._cache[sql] = CacheEntry(result=result, expires_at=now() + self._ttl)
```

### Key Decisions
- 时间窗口在规则级别配置，不同规则可以有不同的窗口
- 时间约束通过 Agent prompt 注入（不是硬改 SQL），让 AI 自己加 WHERE
- 大表策略通过 system prompt 指导 AI 行为
- 冷数据表标记在 schema context 中提示（不完全屏蔽，趋势分析可能需要）
- 缓存为可选特性，默认关闭

## Dependencies
- T20（DBHub 集成）— 数据库连接
- T21（Schema 防幻觉）— schema context 扩展
- T17（AI Agent）— Agent prompt 扩展

## File Changes
- `src/order_guard/engine/agent.py` — 时间约束 + 大表策略注入
- `src/order_guard/mcp/schema.py` — 冷数据表标记
- `src/order_guard/mcp/cache.py` — 查询缓存（可选）
- `src/order_guard/models/tables.py` — AlertRule 增加 data_window 字段
- `src/order_guard/config/settings.py` — 冷数据表配置
- `config.example.yaml` — 时间窗口 + 冷数据表示例
- `alembic/versions/` — 迁移脚本
- `tests/test_hot_cold.py` — 单元测试

## Tasks
- [ ] T22.1: AlertRule 增加 data_window 字段 + DB 迁移
- [ ] T22.2: 实现时间约束 prompt 生成 + 注入 Agent
- [ ] T22.3: 实现大表分步查询策略 prompt
- [ ] T22.4: 实现冷数据表标记（schema context 中标注）
- [ ] T22.5: 实现查询缓存（可选，默认关闭）
- [ ] T22.6: 更新 config.example.yaml
- [ ] T22.7: 编写单元测试
