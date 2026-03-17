# T18: MCP Connector + Pipeline 适配 — 验收标准

## 验收步骤

### 1. 数据模型
- [ ] AlertRule 表新增 connector_type 和 mcp_server 字段
- [ ] Alembic 迁移脚本执行无报错（upgrade + downgrade）
- [ ] 现有规则数据兼容（connector_type 默认 "legacy"）

### 2. 规则配置
- [ ] YAML 中可配置 connector_type: "mcp" 的规则
- [ ] mcp_server 字段正确关联 mcp_servers 配置中的 name
- [ ] 传统规则（无 connector_type 字段）继续正常工作

### 3. Pipeline 分支
- [ ] connector_type == "mcp" 的规则走 Agent 流程
- [ ] connector_type == "legacy" 或空的规则走传统 Connector 流程
- [ ] 两种流程输出格式一致（AnalyzerOutput）
- [ ] 告警推送逻辑对两种来源无差异处理

### 4. CLI 支持
```bash
# 手动执行 MCP 规则
uv run order-guard run --rule rule-warehouse-check --dry-run
```
- [ ] run 命令能执行 MCP 类型规则
- [ ] --dry-run 模式正常工作

### 5. 兼容性
- [ ] 所有 v1 规则（mock connector）继续正常运行
- [ ] 所有现有测试通过

### 6. 集成测试
```bash
uv run pytest tests/test_mcp_pipeline.py -v
```
- [ ] 测试通过
