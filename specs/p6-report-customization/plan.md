# P6: 报告模板定制

## Context
当前定时报告（N6）的内容完全由 LLM 根据 `focus` 字段自由发挥。问题：
- 用户无法精确控制报告包含哪些章节
- 每次生成的报告结构不一致
- 无法通过对话调整报告内容（必须改 config.yaml）

本任务让用户通过对话定制报告模板，同时保持报告结构一致性。

## Scope

### In Scope
- ReportConfig 模型扩展：新增 `sections`（章节列表）和 `kpis`（关键指标定义）
- 2 个新 Agent 工具：`manage_report`、`preview_report`
- 报告生成逻辑适配：按 sections 分段生成
- 对话场景覆盖：查看/修改/预览报告配置

### Not In Scope
- 报告模板市场（预设模板选择）
- 报告导出为 PDF / Excel
- 报告邮件发送
- 报告订阅管理（谁收哪份报告）

## Design

### 数据模型变更

ReportConfig 表新增字段：
```python
class ReportConfig(SQLModel, table=True):
    # ... 现有字段 ...
    sections: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    # 示例：[
    #   {"title": "销售概况", "prompt": "统计总销售额、订单数、客单价", "datasource": "erp_mysql"},
    #   {"title": "库存预警", "prompt": "列出库存低于安全线的SKU", "datasource": "erp_mysql"},
    #   {"title": "退货分析", "prompt": "统计退货率和退货原因分布", "datasource": "erp_mysql"}
    # ]
    kpis: list[dict] = Field(default_factory=list, sa_column=Column(JSON))
    # 示例：[
    #   {"name": "总销售额", "sql": "SELECT SUM(amount) FROM orders WHERE ...", "format": "currency"},
    #   {"name": "订单数", "sql": "SELECT COUNT(*) FROM orders WHERE ...", "format": "number"}
    # ]
    template_style: str = "standard"   # "standard" / "brief" / "detailed"
```

### 报告生成适配

现有流程：
```
focus prompt → Agent 自由查数据 → LLM 生成全文
```

新流程（有 sections 时）：
```
for section in sections:
    section.prompt → Agent 查数据 → LLM 生成段落
合并所有段落 → 加开头/结尾摘要 → 完整报告
```

无 sections 时保持现有逻辑（向后兼容）。

### 工具定义

#### manage_report
```python
async def manage_report(
    action: str,                       # "list" / "get" / "update"
    report_id: str | None = None,
    changes: dict | None = None        # update 时传入
) -> dict:
    """管理报告配置"""
    # list：返回所有报告配置
    # get：返回单个报告详情（含 sections 和 kpis）
    # update：更新报告配置（name, schedule, sections, kpis, focus, enabled）
```

Tool Schema:
```json
{
  "name": "manage_report",
  "description": "管理定时报告配置。支持查看所有报告（action=list）、查看单个报告详情（action=get）、修改报告配置（action=update）。修改时可调整报告章节、关键指标、发送频率等。",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["list", "get", "update"],
        "description": "操作类型"
      },
      "report_id": {
        "type": "string",
        "description": "报告 ID。get 和 update 时必传"
      },
      "changes": {
        "type": "object",
        "description": "更新内容（action=update 时传入）",
        "properties": {
          "name": {"type": "string"},
          "schedule": {"type": "string", "description": "cron 表达式"},
          "focus": {"type": "string"},
          "enabled": {"type": "boolean"},
          "template_style": {
            "type": "string",
            "enum": ["standard", "brief", "detailed"]
          },
          "sections": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "title": {"type": "string"},
                "prompt": {"type": "string"},
                "datasource": {"type": "string"}
              }
            }
          },
          "kpis": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "name": {"type": "string"},
                "sql": {"type": "string"},
                "format": {"type": "string", "enum": ["number", "currency", "percent"]}
              }
            }
          }
        }
      }
    },
    "required": ["action"]
  }
}
```

#### preview_report
```python
async def preview_report(
    report_id: str
) -> dict:
    """预览报告，不推送"""
    # 按当前配置生成报告内容，但不推送
    # 返回报告全文 + token 用量 + 耗时
```

Tool Schema:
```json
{
  "name": "preview_report",
  "description": "预览报告内容，按当前配置生成但不推送。用于验证报告配置是否合理。",
  "input_schema": {
    "type": "object",
    "properties": {
      "report_id": {
        "type": "string",
        "description": "报告 ID，从 manage_report(action=list) 获取"
      }
    },
    "required": ["report_id"]
  }
}
```

### 写操作拦截
- `manage_report`（action=update）属于写操作，加入 `WRITE_TOOLS`
- `preview_report` 是只读，不拦截

### 对话场景示例
```
用户：帮我改一下日报，重点关注库存和退货
Agent：调用 manage_report(action="get", report_id="daily-report")
Agent：当前日报配置：focus="销量、库存、告警汇总"，无自定义章节。我来帮你设置具体章节：
Agent：调用 manage_report(action="update", report_id="daily-report", changes={
    "sections": [
        {"title": "库存预警", "prompt": "列出库存低于安全线的SKU，按紧急程度排序", "datasource": "erp_mysql"},
        {"title": "退货分析", "prompt": "统计退货率趋势，列出退货率最高的SKU和退货原因", "datasource": "erp_mysql"},
        {"title": "今日概况", "prompt": "汇总今日订单数、销售额等核心指标", "datasource": "erp_mysql"}
    ]
})

用户：预览一下看看效果
Agent：调用 preview_report(report_id="daily-report")
Agent：[展示报告预览内容]
```

## Dependencies
- N6（定时报告）— 在现有 reporter.py 基础上扩展
- N12（统一 Agent）— 新工具注册

## File Changes
- `src/order_guard/models/tables.py` — ReportConfig 新增 sections / kpis / template_style
- `src/order_guard/tools/report_tools.py` — 新建，manage_report + preview_report
- `src/order_guard/engine/reporter.py` — 适配 sections 分段生成
- `src/order_guard/engine/agent.py` — 注册新工具
- `alembic/versions/xxx_add_report_sections.py` — 迁移脚本
- `tests/test_reporter.py` — 扩展测试
- `tests/test_report_tools.py` — 新增测试

## Tasks
- [ ] P6.1: ReportConfig 模型新增 sections / kpis / template_style + Alembic 迁移
- [ ] P6.2: reporter.py 适配 sections 分段生成逻辑
- [ ] P6.3: 实现 manage_report 工具（list / get / update）
- [ ] P6.4: 实现 preview_report 工具（生成不推送）
- [ ] P6.5: manage_report(update) 加入 WRITE_TOOLS
- [ ] P6.6: 注册到统一 Agent 工具集
- [ ] P6.7: 编写单元测试
- [ ] P6.8: 全量回归测试
