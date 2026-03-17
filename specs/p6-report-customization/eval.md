# P6: 报告模板定制 — 验收标准

## 验收步骤

### 1. 数据模型
- [ ] ReportConfig 表新增 sections 字段（JSON 数组）
- [ ] ReportConfig 表新增 kpis 字段（JSON 数组）
- [ ] ReportConfig 表新增 template_style 字段（string）
- [ ] Alembic upgrade/downgrade 执行无报错
- [ ] 现有报告配置（无 sections）正常加载（向后兼容）

### 2. manage_report — list
- [ ] 返回所有报告配置
- [ ] 包含：id, name, schedule（人类可读）, enabled, sections 数量
- [ ] 无报告时 hint 说明

### 3. manage_report — get
- [ ] 返回完整报告配置（含 sections 和 kpis 详情）
- [ ] report_id 不存在 → error + hint

### 4. manage_report — update
- [ ] 只更新传入的字段
- [ ] sections 正确保存到 DB
- [ ] kpis 正确保存到 DB
- [ ] schedule 修改 → 调度任务同步更新
- [ ] enabled 修改 → 调度任务启用/禁用
- [ ] 写操作经过确认拦截

### 5. 报告生成 — sections 模式
- [ ] 有 sections → 按章节顺序生成
- [ ] 每个 section 使用自己的 prompt 和 datasource
- [ ] 章节之间有标题分隔
- [ ] 报告开头有日期和整体摘要
- [ ] 报告结尾有统计信息（查询次数、耗时）

### 6. 报告生成 — 向后兼容
- [ ] 无 sections → 使用现有 focus 逻辑（LLM 自由发挥）
- [ ] 现有报告配置不受影响

### 7. 报告生成 — kpis
- [ ] kpis 定义的指标在报告中优先展示
- [ ] 格式正确（number: 千分位，currency: $符号+小数，percent: 百分号）
- [ ] SQL 执行失败 → 显示"数据获取失败"而非报错

### 8. preview_report
- [ ] 按当前配置生成报告全文
- [ ] 不推送到任何渠道
- [ ] 返回报告内容 + token 用量 + 耗时
- [ ] report_id 不存在 → error + hint

### 9. Agent 集成
- [ ] manage_report 和 preview_report 注册到 Agent 工具集
- [ ] Agent 能通过对话正确调用
- [ ] manage_report(update) 在 WRITE_TOOLS 中

### 10. 返回信封
- [ ] 成功返回 `{"data": ..., "hint": "..."}`
- [ ] 失败返回 `{"error": "...", "hint": "..."}`

### 11. 单元测试
```bash
uv run pytest tests/test_report_tools.py tests/test_reporter.py -v
```
- [ ] 测试通过

### 12. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过，零回归
