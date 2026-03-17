# N9: 规则管理工具集 — 验收标准

## 验收步骤

### 1. 统一返回信封
- [ ] 所有工具成功时返回 `{"data": ..., "hint": "..."}`
- [ ] 所有工具失败时返回 `{"error": "...", "hint": "..."}`
- [ ] hint 内容是可行动的下一步建议

### 2. list_rules
- [ ] 返回所有规则列表（name, datasource, schedule_human, enabled, last_run, alerts_24h）
- [ ] schedule 显示为人类可读格式（如"每天 9:00"而非 cron）
- [ ] 无规则时 hint 引导创建

### 3. create_rule — 正常流程
```
create_rule(
  name="库存低于安全线",
  mcp_server="erp-mysql",
  prompt_template="查询库存低于安全线的SKU...",
  schedule="0 9 * * *"
)
```
- [ ] 规则写入 DB
- [ ] 调度任务动态注册（无需重启）
- [ ] 返回规则详情 + hint

### 4. create_rule — 校验失败
- [ ] cron 不合法 → 返回 error + 修正建议（含示例）
- [ ] mcp_server 不存在 → 返回 error + 可用数据源列表
- [ ] name 重复 → 返回 error + 建议改名
- [ ] 必填字段缺失 → 返回 error + 缺少哪些字段

### 5. update_rule
- [ ] 只更新传入的字段，其他不变
- [ ] 修改 schedule → 旧调度移除 + 新调度注册
- [ ] 修改 enabled=false → 调度移除
- [ ] 修改 enabled=true → 调度注册
- [ ] rule_id 不存在 → 返回 error

### 6. delete_rule
- [ ] 规则从 DB 删除
- [ ] 调度任务移除
- [ ] yaml 来源规则 → hint 提示重启会重新同步
- [ ] rule_id 不存在 → 返回 error

### 7. test_rule
- [ ] 执行规则分析逻辑
- [ ] 不推送告警
- [ ] 返回 alerts_found, alerts 详情, summary, duration_ms
- [ ] rule_id 不存在 → 返回 error

### 8. 单元测试
```bash
uv run pytest tests/test_rule_tools.py -v
```
- [ ] 测试通过

### 9. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过
