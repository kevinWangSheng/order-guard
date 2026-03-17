# N8: 业务知识注入 — 验收标准

## 验收步骤

### 1. 配置加载
- [ ] config.example.yaml 包含 business_context 示例
- [ ] Settings 正确解析 business_context 字段
- [ ] business_context 为空时不影响系统运行

### 2. Agent 注入
- [ ] Agent system prompt 包含 business_context 内容
- [ ] 分析结果能体现业务背景（如提到毛利率目标、当前策略等）
- [ ] business_context 为空时 Agent 正常工作（无注入）

### 3. 对话更新
```
用户："记住，我们下周在 TEMU 做促销"
```
- [ ] 系统识别为知识更新（不是数据查询）
- [ ] 更新内容持久化到 DB
- [ ] 回复用户确认更新成功
- [ ] 后续对话中 Agent 能引用该知识

### 4. 持久化
- [ ] DB 迁移执行无报错
- [ ] 配置文件中的初始值 + DB 中的更新记录正确合并
- [ ] 重启后对话更新的知识仍然存在

### 5. 长度限制
- [ ] business_context 超过 2000 字符时有提示或截断

### 6. 单元测试
```bash
uv run pytest tests/test_business_context.py -v
```
- [ ] 测试通过
