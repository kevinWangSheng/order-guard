# T25: 清理传统数据流 — 验收标准

## 前置条件
- [ ] T20-T24 全部通过验收
- [ ] 所有规则已迁移到 MCP 模式

## 验收步骤

### 1. 代码清理
- [ ] connectors 包已移除（base / mock / csv / registry）
- [ ] MetricsEngine 和 SummaryBuilder 已移除
- [ ] pipeline 中无传统分支代码
- [ ] 无残留的 import 引用

### 2. 数据模型
- [ ] AlertRule 已移除 connector_id / data_type 字段
- [ ] Alembic 迁移执行无报错

### 3. 功能验证
```bash
uv run order-guard run --rule rule-mcp-inventory --dry-run
```
- [ ] MCP Agent 流程正常工作
- [ ] 所有现有 MCP 规则正常执行
- [ ] 告警推送正常

### 4. 测试
```bash
uv run pytest -v
```
- [ ] 所有测试通过（无传统流程相关测试失败）
- [ ] 无残留的传统流程测试文件
