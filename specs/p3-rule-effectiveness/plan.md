# P3: 规则效果评估

## Context
用户通过对话创建了监控规则，但之后无法知道这条规则"表现如何"——触发了多少次？有多少是误报？是否值得保留？

没有效果反馈，规则只会越来越多、越来越乱，误报疲劳导致用户忽略真正的告警。

本任务依赖 P1（告警闭环），因为误报率需要 `resolution=false_positive` 的数据。

## Scope

### In Scope
- 增强 `list_rules` 工具返回值：新增触发次数、上次触发时间、误报率等统计
- 1 个新 Agent 工具：`get_rule_stats`（单条规则深度统计）
- 基于 Alert + TaskRun 表聚合，不新增表

### Not In Scope
- 规则自动调优（AI 自动修改 prompt）
- 规则评分/排名
- A/B 测试（同一规则两种 prompt 对比）

## Design

### list_rules 增强

现有 `list_rules` 返回：
```python
{"name", "datasource", "schedule_human", "enabled", "last_run", "alerts_24h"}
```

新增字段：
```python
{
    # ... 现有字段 ...
    "trigger_count_7d": 5,           # 最近 7 天触发告警次数
    "false_positive_count_7d": 1,    # 最近 7 天误报次数
    "false_positive_rate": 0.20,     # 误报率 = false_positive / total
    "last_triggered_at": "2026-03-12T09:00:00",  # 上次触发告警时间
    "run_count_7d": 14,              # 最近 7 天执行次数
    "run_success_rate": 1.0,         # 执行成功率
}
```

### 新增工具：get_rule_stats

```python
async def get_rule_stats(
    rule_id: str,
    time_range: str = "30d"
) -> dict:
    """查询单条规则的详细效果统计"""
    # 返回：
    # basic: name, schedule_human, enabled, created_at, source
    # execution: total_runs, success_runs, failed_runs, success_rate, avg_duration_ms
    # alerts: total_alerts, by_severity, by_resolution, false_positive_rate
    # trend: 最近 N 天每天的告警数（简单数组）
    # token_usage: total_tokens, total_cost_usd（如果 P2 已完成）
```

Tool Schema:
```json
{
  "name": "get_rule_stats",
  "description": "查询单条规则的详细效果统计。包括执行情况（成功率、耗时）、告警情况（数量、级别分布、误报率）、趋势（每日告警数）。用于评估规则质量和调优决策。",
  "input_schema": {
    "type": "object",
    "properties": {
      "rule_id": {
        "type": "string",
        "description": "规则 ID，从 list_rules 获取"
      },
      "time_range": {
        "type": "string",
        "enum": ["7d", "30d", "90d"],
        "description": "统计时间范围，默认 30d"
      }
    },
    "required": ["rule_id"]
  }
}
```

### 数据聚合逻辑

所有统计基于现有表，不新增表：
- **告警统计**：`Alert` 表，按 `rule_id` + `created_at` 聚合
- **误报率**：`Alert` 表，`resolution = 'false_positive'` 的比例（依赖 P1）
- **执行统计**：`TaskRun` 表，按 `rule_id` + `started_at` 聚合
- **Token 用量**：`LLMUsageLog` 表（如果 P2 已完成，否则返回 null）

### 对话场景示例
```
用户：这些规则效果怎么样
Agent：调用 list_rules()
Agent：共 5 条规则。"退货异常"规则误报率 40%，建议调整阈值。"缺货检测"表现良好（0% 误报）。

用户：退货异常这条规则详细看看
Agent：调用 get_rule_stats(rule_id="rule-return-rate", time_range="30d")
Agent：最近 30 天执行 60 次，触发 12 条告警，其中 5 条标记为误报（误报率 42%）。
      告警主要集中在周一（促销后退货高峰），建议调整 prompt 排除促销后 3 天的数据。

用户：帮我调一下这条规则，排除促销后3天的退货数据
Agent：调用 update_rule(...)
```

### hint 智能建议

`list_rules` 的 hint 根据规则效果动态生成：
- 误报率 > 30%：提示"规则 X 误报率较高，建议调整"
- 7 天未触发：提示"规则 X 最近 7 天未触发，确认是否仍需要"
- 执行成功率 < 90%：提示"规则 X 执行失败率较高，检查数据源连接"

## Dependencies
- **P1（告警闭环）**：必须先完成。误报率依赖 `resolution=false_positive` 数据
- P2（LLM 用量）：可选依赖。如果 P2 完成，get_rule_stats 增加 token 用量字段
- N9（规则工具）：增强 list_rules 返回值

## File Changes
- `src/order_guard/tools/rule_tools.py` — 增强 list_rules + 新增 get_rule_stats
- `src/order_guard/engine/agent.py` — 注册 get_rule_stats 工具
- `tests/test_rule_tools.py` — 扩展测试

## Tasks
- [ ] P3.1: 告警统计聚合函数（按 rule_id 统计告警数、误报数、级别分布）
- [ ] P3.2: 执行统计聚合函数（按 rule_id 统计运行次数、成功率、耗时）
- [ ] P3.3: 增强 list_rules 返回值（新增统计字段）
- [ ] P3.4: 实现 get_rule_stats（完整统计 + 趋势 + 格式化）
- [ ] P3.5: 智能 hint 生成（误报率高、长期未触发、执行失败率高）
- [ ] P3.6: 注册到统一 Agent 工具集
- [ ] P3.7: 编写单元测试
- [ ] P3.8: 全量回归测试
