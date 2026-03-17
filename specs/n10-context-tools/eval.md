# N10: 业务知识工具集 — 验收标准

## 验收步骤

### 1. 数据模型
- [ ] BusinessContext 表包含 category 和 expires_at 字段
- [ ] DB 迁移成功执行
- [ ] category 只接受合法枚举值

### 2. list_context
- [ ] 返回所有未过期的业务知识
- [ ] 已过期的自动排除
- [ ] 传 category 参数时只返回对应分类
- [ ] 无记录时 hint 引导添加

### 3. add_context — 正常流程
```
add_context(content="3月全线提价5%", category="strategy", expires_at="30d")
```
- [ ] 写入 DB，category 和 expires_at 正确
- [ ] expires_at="30d" 解析为 now + 30 天
- [ ] 返回新记录 + hint

### 4. add_context — 校验失败
- [ ] content 为空 → error
- [ ] category 不在枚举中 → error + 可选分类列表
- [ ] 已达 20 条上限 → error + 提示清理

### 5. delete_context
- [ ] 删除成功 → data + hint
- [ ] context_id 不存在 → error

### 6. System Prompt 注入
- [ ] build_context_injection 按分类分组输出
- [ ] 超过 20 条时截断
- [ ] 超过 1000 tokens 时截断
- [ ] 已过期的不注入

### 7. Config 初始加载
- [ ] 启动时 config.yaml business_context 写入 DB（source="config"）
- [ ] 重复启动不会重复写入

### 8. 单元测试
```bash
uv run pytest tests/test_context_tools.py -v
```
- [ ] 测试通过

### 9. 全量回归
```bash
uv run pytest -v
```
- [ ] 所有测试通过
