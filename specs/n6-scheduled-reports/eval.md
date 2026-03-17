# N6: 定时报告 — 验收标准

## 验收步骤

### 1. 配置
- [ ] config.example.yaml 包含 reports 配置示例
- [ ] YAML 报告配置正确加载
- [ ] 报告配置同步到 DB

### 2. 报告生成
```bash
uv run order-guard reports run --report daily-report --dry-run
```
- [ ] Agent 自动查询相关数据（多轮 Tool Call）
- [ ] LLM 生成经营摘要（包含 focus 中要求的维度）
- [ ] 摘要内容包含具体数据（不是泛泛而谈）
- [ ] dry-run 模式不推送，只输出报告内容

### 3. 报告推送
```bash
uv run order-guard reports run --report daily-report
```
- [ ] 报告推送到飞书/企微（格式化卡片）
- [ ] 推送成功记录到 report_history 表

### 4. 定时调度
- [ ] 报告按 schedule 配置的 cron 表达式定时执行
- [ ] 报告任务和规则任务互不干扰
- [ ] 报告执行失败不影响其他任务

### 5. CLI 命令
```bash
uv run order-guard reports list
uv run order-guard reports run --report {id} --dry-run
```
- [ ] list 显示所有报告配置
- [ ] run 手动触发报告生成

### 6. DB 记录
- [ ] report_history 正确记录每次报告生成
- [ ] 记录包含 content、status、token_usage、duration_ms

### 7. 单元测试
```bash
uv run pytest tests/test_reporter.py -v
```
- [ ] 测试通过

### 8. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过
