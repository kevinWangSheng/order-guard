# P1: 告警闭环（Alert Lifecycle）

## Context
当前告警推送出去后没有后续动作。Alert 表只有 `pending/sent/failed/silenced` 四种状态，全部是系统自动设置的。用户无法标记"已处理"、"忽略"、"误报"，也无法查看处理率等统计。

告警没有闭环 = 告警没有价值。没有反馈数据，后续的规则调优（P3）也无从做起。

## Scope

### In Scope
- Alert 模型扩展：新增 `resolution`、`resolved_by`、`resolved_at` 字段
- 2 个新 Agent 工具：`handle_alert`、`get_alert_stats`
- 统一返回信封 `{data, hint}` / `{error, hint}`
- 对话场景覆盖：标记处理、批量标记、统计查询
- Alembic 迁移

### Not In Scope
- 告警工单系统（Jira/飞书多维表格联动）
- 告警升级（超时未处理自动升级）
- 告警归因分析（AI 分析根因）

## Design

### 数据模型变更

Alert 表新增字段：
```python
class Alert(SQLModel, table=True):
    # ... 现有字段 ...
    resolution: str | None = None      # handled / ignored / false_positive
    resolved_by: str = ""              # user_id
    resolved_at: datetime | None = None
    note: str = ""                     # 处理备注
```

status 字段保持不变（pending/sent/failed/silenced），resolution 是独立的"业务处理状态"。

### 工具定义

#### handle_alert
```python
async def handle_alert(
    alert_id: str | None = None,       # 单条处理
    rule_id: str | None = None,        # 按规则批量处理
    time_range: str | None = None,     # 配合 rule_id 批量
    resolution: str = "handled",       # handled / ignored / false_positive
    note: str = ""                     # 处理备注
) -> dict:
    """标记告警处理状态"""
    # alert_id 和 rule_id 二选一
    # resolution 枚举校验
    # 批量处理返回受影响条数
    # 已处理的告警不可重复处理（返回 hint 提示）
```

Tool Schema:
```json
{
  "name": "handle_alert",
  "description": "标记告警的处理状态。可单条处理（传 alert_id）或按规则批量处理（传 rule_id）。resolution 可选：handled（已处理）、ignored（已忽略）、false_positive（误报）。标记为误报的告警会影响规则效果评估。",
  "input_schema": {
    "type": "object",
    "properties": {
      "alert_id": {
        "type": "string",
        "description": "告警 ID，从 list_alerts 获取。和 rule_id 二选一"
      },
      "rule_id": {
        "type": "string",
        "description": "规则 ID，批量处理该规则下所有未处理告警。和 alert_id 二选一"
      },
      "time_range": {
        "type": "string",
        "enum": ["24h", "7d", "30d"],
        "description": "配合 rule_id 使用，限定时间范围。不传则处理所有未处理的"
      },
      "resolution": {
        "type": "string",
        "enum": ["handled", "ignored", "false_positive"],
        "description": "处理结果。handled=已处理，ignored=已忽略，false_positive=误报。默认 handled"
      },
      "note": {
        "type": "string",
        "description": "处理备注，可选"
      }
    },
    "required": ["resolution"]
  }
}
```

#### get_alert_stats
```python
async def get_alert_stats(
    time_range: str = "7d",            # 24h / 7d / 30d
    rule_id: str | None = None         # 按规则筛选
) -> dict:
    """查询告警统计数据"""
    # 返回：
    # total, by_severity, by_resolution, unresolved_count
    # resolution_rate（处理率 = 已处理 / 总数）
    # avg_resolution_time_hours
    # top_rules（告警数 top 5 规则）
```

Tool Schema:
```json
{
  "name": "get_alert_stats",
  "description": "查询告警统计数据。包括告警数量、级别分布、处理率、平均处理时间、告警最多的规则等。用于了解整体告警健康度。",
  "input_schema": {
    "type": "object",
    "properties": {
      "time_range": {
        "type": "string",
        "enum": ["24h", "7d", "30d"],
        "description": "统计时间范围，默认 7d"
      },
      "rule_id": {
        "type": "string",
        "description": "按规则筛选统计。不传则统计所有规则"
      }
    },
    "required": []
  }
}
```

### 写操作拦截
- `handle_alert` 属于写操作，需要加入 Agent 的 `WRITE_TOOLS` 集合
- 但标记处理是低风险操作，考虑 **不拦截**（与 delete_rule 不同，标记不可逆但无破坏性）
- 最终决策：**不拦截**，直接执行。如果用户标记错了可以重新标记。

### 对话场景示例
```
用户：把最近的缺货告警都标记为已处理
Agent：调用 handle_alert(rule_id="rule-erp-stockout", resolution="handled")
Agent：已处理 3 条缺货告警。

用户：上周告警处理率怎么样
Agent：调用 get_alert_stats(time_range="7d")
Agent：最近 7 天共 15 条告警，处理率 73%。其中 2 条标记为误报（来自"退货异常"规则）。

用户：那个退货异常的告警是误报，标记一下
Agent：调用 handle_alert(alert_id="xxx", resolution="false_positive", note="退货数据口径问题")
```

## Dependencies
- N11（告警历史工具）— 复用 alert_tools.py，在同一文件扩展
- N12（统一 Agent）— 新工具注册到 Agent 工具集

## File Changes
- `src/order_guard/models/tables.py` — Alert 模型新增 resolution / resolved_by / resolved_at / note
- `src/order_guard/tools/alert_tools.py` — 新增 handle_alert + get_alert_stats
- `src/order_guard/engine/agent.py` — 注册新工具
- `alembic/versions/xxx_add_alert_resolution.py` — 迁移脚本
- `tests/test_alert_tools.py` — 扩展测试

## Tasks
- [ ] P1.1: Alert 模型新增 resolution / resolved_by / resolved_at / note 字段
- [ ] P1.2: Alembic 迁移脚本
- [ ] P1.3: 实现 handle_alert（单条 + 批量 + 校验 + hint）
- [ ] P1.4: 实现 get_alert_stats（统计聚合 + 格式化 + hint）
- [ ] P1.5: 注册到统一 Agent 工具集
- [ ] P1.6: 更新 list_alerts 返回值增加 resolution 字段
- [ ] P1.7: 编写单元测试
- [ ] P1.8: 全量回归测试
