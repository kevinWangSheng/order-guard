# T24: 飞书 Bot 对话 — 验收标准

## 验收步骤

### 1. 飞书 Event 回调
- [ ] FastAPI 路由 POST /api/feishu/event 正常响应
- [ ] URL 验证（challenge）正常返回
- [ ] 接收 @机器人 文本消息正常解析

### 2. 消息处理
- [ ] 收到消息后异步处理（不阻塞飞书回调）
- [ ] Agent 正确连接到指定 MCP Server
- [ ] Agent 查询数据并返回分析结果
- [ ] 飞书群收到卡片格式回复

### 3. 多轮对话
- [ ] 连续提问保持上下文（如"查库存" → "哪个最缺货？"）
- [ ] 30 分钟不活跃后上下文自动清除
- [ ] 最多保留 10 轮对话

### 4. 权限控制
- [ ] 无权限用户收到拒绝提示
- [ ] 有权限用户能正常查询
- [ ] 不同用户可配置不同数据源权限

### 5. 配置
- [ ] config.example.yaml 包含 feishu_bot 配置段
- [ ] .env.example 包含飞书应用凭证
- [ ] docs/feishu-bot-setup.md 飞书应用创建指南完整

### 6. 单元测试
```bash
uv run pytest tests/test_feishu_bot.py -v
```
- [ ] 测试通过（mock 飞书 API）
