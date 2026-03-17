# T25: 清理传统数据流

## Context
v3 全面切换到 MCP Agent 流程后，v1 的传统数据流（固定 Connector → MetricsEngine → SummaryBuilder → LLM 单次调用）不再需要。本任务在确认 MCP Agent 稳定后，移除传统流程代码，简化架构。

## Scope

### In Scope
- 移除 MetricsEngine（指标计算模块）
- 移除 SummaryBuilder（摘要生成模块）
- 移除 BaseConnector 固定方法（get_orders / get_inventory / get_sales）
- 移除 MockConnector
- 移除 CSVConnector（如确认不再需要）
- 移除 ConnectorRegistry
- 移除 pipeline 中的传统分支（connector_type == "legacy" 逻辑）
- 移除 AlertRule 中 connector_id / data_type 字段
- 清理相关测试和配置

### Not In Scope
- MCP Agent 流程的修改
- 新功能开发

## Precondition
**本任务仅在以下条件全部满足后执行：**
- T20-T24 全部完成并验证通过
- MCP Agent 流程在生产环境稳定运行
- 确认没有规则依赖传统 Connector

## Design

### 待移除文件
```
src/order_guard/connectors/base.py        — BaseConnector ABC
src/order_guard/connectors/mock.py        — MockConnector
src/order_guard/connectors/csv_connector.py — CSVConnector（待确认）
src/order_guard/connectors/registry.py    — ConnectorRegistry
src/order_guard/engine/metrics.py         — MetricsEngine
src/order_guard/engine/metrics_summary.py — SummaryBuilder
tests/test_mock_connector.py
tests/test_csv_connector.py
tests/test_metrics.py
```

### 待修改文件
```
src/order_guard/scheduler/jobs.py  — 移除传统分支
src/order_guard/models/tables.py   — AlertRule 移除 connector_id / data_type
src/order_guard/engine/rules.py    — 规则加载移除传统字段
src/order_guard/cli.py             — 移除传统 connector 相关命令
config.example.yaml                — 移除 connectors 配置段
alembic/versions/                  — 迁移脚本
```

### Key Decisions
- 此任务是一次性清理，不可逆
- 执行前必须确认所有规则已迁移到 MCP 模式
- DB 迁移移除字段前做数据备份

## Dependencies
- T20-T24 全部完成并验证通过

## Tasks
- [ ] T25.1: 确认所有规则已迁移到 MCP 模式
- [ ] T25.2: 移除 connectors 包（base / mock / csv / registry）
- [ ] T25.3: 移除 engine/metrics.py 和 metrics_summary.py
- [ ] T25.4: 移除 pipeline 传统分支
- [ ] T25.5: AlertRule 模型移除 connector_id / data_type + DB 迁移
- [ ] T25.6: 清理配置文件（移除 connectors 段）
- [ ] T25.7: 清理相关测试
- [ ] T25.8: 验证 MCP Agent 流程无回归
