# N9: 规则管理工具集

## Context
N4 实现了自然语言配规则，但逻辑耦合在 `rule_agent.py` 和 `feishu.py` 的意图分支中。v4 改版决定去掉意图分类，改为单 Agent + 统一工具集。

本任务将规则管理能力拆为 5 个独立工具函数，供 Agent 直接调用。工具只负责数据校验、CRUD 和返回 hint，不包含调用策略。

## Scope

### In Scope
- 5 个规则管理工具函数：list_rules / create_rule / update_rule / delete_rule / test_rule
- 统一返回信封 `{data, hint}` / `{error, hint}`
- 工具内部校验（cron 合法性、mcp_server 存在性、表名存在性）
- 校验失败返回可行动错误 + 修正建议
- 工具 JSON Schema 定义（供 LLM tool calling 使用）
- 调度集成（create 后动态注册、delete 后移除）

### Not In Scope
- 调用策略（放 system prompt，N12 负责）
- 写操作确认拦截（放 Agent 编排层，N12 负责）
- 规则版本管理
- 规则模板市场

## Design

### 文件结构
```
src/order_guard/tools/
├── __init__.py
├── rule_tools.py      ← 本任务
├── context_tools.py   ← N10
└── data_tools.py      ← N12 迁移
```

### 统一返回信封
所有工具统一返回格式：
```python
# 成功
{"data": {...}, "hint": "下一步建议"}

# 错误
{"error": "错误描述", "hint": "修正建议"}
```

### 工具定义

#### list_rules
```python
def list_rules() -> dict:
    """列出所有已配置的监控规则"""
    # 返回：
    # data: [{ name, datasource, schedule_human, enabled, last_run, alerts_24h }]
    # hint: 动态生成，如 "共 3 条规则，其中 1 条已禁用。可以创建新规则或修改已有规则。"
```

Tool Schema:
```json
{
  "name": "list_rules",
  "description": "列出所有已配置的监控规则。返回规则名称、数据源、执行频率、启用状态、上次运行时间、最近24小时告警数。",
  "input_schema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

#### create_rule
```python
def create_rule(
    name: str,
    mcp_server: str,
    prompt_template: str,
    schedule: str,           # cron 表达式
    data_window: str = "7d",
    enabled: bool = True
) -> dict:
    """创建一条新的监控规则"""
    # 内部校验：
    # 1. croniter 验证 schedule 合法性
    # 2. 检查 mcp_server 是否在已配置的数据源列表中
    # 3. name 不为空且不重复
    #
    # 校验通过：写入 DB + 动态注册调度 → 返回 data + hint
    # 校验失败：返回 error + hint（修正建议）
```

Tool Schema:
```json
{
  "name": "create_rule",
  "description": "创建一条新的监控规则。需要提供规则名称、数据源、分析 prompt、cron 调度表达式。创建前请先用 list_datasources 和 get_schema 了解可用的数据源和表结构。",
  "input_schema": {
    "type": "object",
    "properties": {
      "name": {
        "type": "string",
        "description": "规则名称，如 '库存低于安全线检查'"
      },
      "mcp_server": {
        "type": "string",
        "description": "数据源 ID，从 list_datasources 获取"
      },
      "prompt_template": {
        "type": "string",
        "description": "分析 prompt 模板，包含具体的 SQL 查询逻辑和分析要求"
      },
      "schedule": {
        "type": "string",
        "description": "cron 表达式，如 '0 9 * * *'（每天9点）、'0 */2 * * *'（每2小时）"
      },
      "data_window": {
        "type": "string",
        "description": "数据时间窗口，如 '7d'、'24h'、'30d'。默认 '7d'"
      },
      "enabled": {
        "type": "boolean",
        "description": "是否立即启用。默认 true"
      }
    },
    "required": ["name", "mcp_server", "prompt_template", "schedule"]
  }
}
```

校验失败示例：
```python
# cron 不合法
{"error": "schedule '0 9 * *' 不是合法的 cron 表达式（缺少一个字段）",
 "hint": "cron 表达式需要 5 个字段：分 时 日 月 周。示例：'0 9 * * *' 表示每天9点。"}

# 数据源不存在
{"error": "数据源 'mysql-erp' 不存在",
 "hint": "可用的数据源：['erp-mysql', 'warehouse-pg']。请使用 list_datasources 查看完整列表。"}
```

#### update_rule
```python
def update_rule(rule_id: int, changes: dict) -> dict:
    """修改已有规则，只传需要改的字段"""
    # changes 可包含：name, mcp_server, prompt_template, schedule, data_window, enabled
    # 校验逻辑同 create_rule
    # 如果修改了 schedule：移除旧调度 + 注册新调度
    # 如果修改了 enabled：enabled=False 移除调度，enabled=True 注册调度
```

Tool Schema:
```json
{
  "name": "update_rule",
  "description": "修改已有监控规则。只需传入要修改的字段。修改前请先用 list_rules 确认规则 ID。",
  "input_schema": {
    "type": "object",
    "properties": {
      "rule_id": {
        "type": "integer",
        "description": "规则 ID，从 list_rules 获取"
      },
      "changes": {
        "type": "object",
        "description": "要修改的字段，如 {\"schedule\": \"0 */2 * * *\", \"enabled\": false}",
        "properties": {
          "name": {"type": "string"},
          "mcp_server": {"type": "string"},
          "prompt_template": {"type": "string"},
          "schedule": {"type": "string"},
          "data_window": {"type": "string"},
          "enabled": {"type": "boolean"}
        }
      }
    },
    "required": ["rule_id", "changes"]
  }
}
```

#### delete_rule
```python
def delete_rule(rule_id: int) -> dict:
    """删除监控规则"""
    # 从 DB 删除 + 移除调度任务
    # 如果是 yaml 来源的规则，hint 提示重启会重新同步
```

Tool Schema:
```json
{
  "name": "delete_rule",
  "description": "删除一条监控规则并移除其定时任务。删除后不可恢复。",
  "input_schema": {
    "type": "object",
    "properties": {
      "rule_id": {
        "type": "integer",
        "description": "规则 ID，从 list_rules 获取"
      }
    },
    "required": ["rule_id"]
  }
}
```

#### test_rule
```python
def test_rule(rule_id: int) -> dict:
    """试运行规则，不推送告警"""
    # 执行规则的分析逻辑，但不触发告警推送
    # 返回：alerts_found, alerts 详情, summary, duration_ms
```

Tool Schema:
```json
{
  "name": "test_rule",
  "description": "试运行一条规则，执行数据分析但不推送告警。用于验证规则是否正确配置。",
  "input_schema": {
    "type": "object",
    "properties": {
      "rule_id": {
        "type": "integer",
        "description": "规则 ID，从 list_rules 获取"
      }
    },
    "required": ["rule_id"]
  }
}
```

### Key Decisions
- 工具函数是纯函数，不感知确认流程（确认由 Agent 编排层拦截，N12 负责）
- 校验逻辑在工具内部完成，不依赖 LLM 判断
- hint 根据操作结果动态生成，引导 Agent 下一步动作
- create_rule 成功后自动注册调度，delete_rule 成功后自动移除调度
- 统一返回信封 `{data, hint}` / `{error, hint}`

## Dependencies
- N1（统一数据访问层）— mcp_server 存在性校验依赖 DataAccessLayer
- T07（规则引擎）— 复用 AlertRule 模型和 RuleManager 的 CRUD
- T09（调度器）— 动态注册/移除调度任务

## File Changes
- `src/order_guard/tools/__init__.py` — 新建 tools 包
- `src/order_guard/tools/rule_tools.py` — 5 个规则工具函数 + Tool Schema 定义
- `tests/test_rule_tools.py` — 单元测试

## Tasks
- [ ] N9.1: 创建 tools 包 + 统一返回信封工具函数
- [ ] N9.2: 实现 list_rules（查询 DB + 格式化 + hint）
- [ ] N9.3: 实现 create_rule（校验 + 写入 DB + 注册调度 + hint）
- [ ] N9.4: 实现 update_rule（部分更新 + 校验 + 调度同步 + hint）
- [ ] N9.5: 实现 delete_rule（删除 + 移除调度 + hint）
- [ ] N9.6: 实现 test_rule（试运行 + 不推送 + 返回结果）
- [ ] N9.7: 定义 5 个工具的 JSON Schema
- [ ] N9.8: 编写单元测试
