# N11: 告警历史工具

## Context
规则触发的告警推送到飞书群后，用户无法通过对话回查历史告警。如果错过推送或想看汇总，没有途径获取。

本任务新增 list_alerts 工具，让 Agent 能查询历史告警记录。

## Scope

### In Scope
- 1 个工具函数：list_alerts
- 支持按规则、时间范围、数量筛选
- 统一返回信封 `{data, hint}` / `{error, hint}`

### Not In Scope
- 告警详情页面（Web UI）
- 告警确认/关闭功能
- 告警统计报表

## Design

### 工具定义

#### list_alerts
```python
def list_alerts(
    rule_id: int | None = None,
    time_range: str | None = None,  # "24h", "7d", "30d"
    limit: int = 20
) -> dict:
    """查询历史告警记录"""
    # 从 Alert 表查询
    # 支持按 rule_id 筛选
    # time_range 解析为 created_at > now - duration
    # 按时间倒序，limit 限制条数
    # 返回：[{ id, rule_name, severity, summary, created_at }]
    # hint 动态生成，如 "最近24小时共 5 条告警，其中 2 条高级别。"
```

Tool Schema:
```json
{
  "name": "list_alerts",
  "description": "查询历史告警记录。可按规则和时间范围筛选。",
  "input_schema": {
    "type": "object",
    "properties": {
      "rule_id": {
        "type": "integer",
        "description": "按规则 ID 筛选，从 list_rules 获取。不传则查所有规则"
      },
      "time_range": {
        "type": "string",
        "enum": ["24h", "7d", "30d"],
        "description": "时间范围。不传则不限"
      },
      "limit": {
        "type": "integer",
        "description": "返回条数上限，默认 20"
      }
    },
    "required": []
  }
}
```

### Key Decisions
- list_alerts 是只读操作，不需要确认拦截
- time_range 用 enum 而非自由文本，减少解析复杂度
- 默认按时间倒序，最新的在前

## Dependencies
- T08（告警系统）— 复用 Alert 模型
- N9（规则工具集）— 复用 tools 包结构和统一返回信封

## File Changes
- `src/order_guard/tools/alert_tools.py` — list_alerts 工具函数 + Tool Schema
- `tests/test_alert_tools.py` — 单元测试

## Tasks
- [ ] N11.1: 实现 list_alerts（查询 + 筛选 + 格式化 + hint）
- [ ] N11.2: 定义 Tool JSON Schema
- [ ] N11.3: 编写单元测试
