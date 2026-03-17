# N8: 业务知识注入

## Context
当前 Agent 分析数据时缺乏公司业务背景，输出的分析和建议过于泛化。例如看到销量下降只能说"可能是市场变化"，而不知道公司最近提了价、在做品牌升级、或者是旺季备货期。

本任务让用户配置业务知识（business_context），注入到 Agent 的 system prompt，让分析更贴合实际业务。

## Scope

### In Scope
- Settings 新增 `business_context` 配置字段
- business_context 注入到 Agent system prompt
- 通过对话更新 business_context（"记住，我们下个月要做品牌升级"）
- business_context 持久化（写入配置文件或 DB）
- config.example.yaml 添加示例

### Not In Scope
- RAG / 向量检索
- 知识库管理界面
- 文档上传解析

## Design

### 配置方式
```yaml
# config.yaml
business_context: |
  公司：跨境电商，主营家居品类
  主要平台：Amazon US、Walmart、TEMU
  毛利率目标：25%以上
  当前策略：清仓低动销SKU，主推新品
  旺季：Q4（10-12月），需提前3个月备货
  近期变动：3月初全线提价5%
```

### 注入方式
```python
# engine/agent.py 的 system prompt 构建
system_prompt = f"""
你是一个企业经营分析助手。

## 公司业务背景
{settings.business_context}

## 数据库结构
{schema_context}

## 分析要求
...
"""
```

### 对话更新
用户在聊天中说"记住，XX"或"更新业务背景：XX"时，系统识别为知识更新指令。

```
用户："记住，我们下周开始在 TEMU 上做促销，预计销量翻倍"

Agent 识别为知识更新 → 追加到 business_context → 回复确认
```

实现方式：
1. 在飞书 Bot 的意图分类中增加 `UPDATE_CONTEXT` 类型
2. 识别后提取知识内容
3. 追加到 DB 中的 business_context 记录
4. 回复用户确认

### 存储
- 初始值从 config.yaml 加载
- 对话更新的内容存入 DB（新增 `business_context` 表或 key-value 表）
- Agent 使用时合并：config 原始值 + DB 中的更新记录

### Key Decisions
- business_context 是纯文本，不做结构化解析——让 LLM 自己理解
- 对话更新采用"追加"模式，不覆盖原始配置
- 长度限制：business_context 总长度不超过 2000 字符（避免挤占 LLM 上下文）

## Dependencies
- 无强依赖，可独立开发
- 如果 N1 已完成，需要在 DataAccessLayer 的工具集中适配

## File Changes
- `src/order_guard/config/settings.py` — 新增 business_context 字段
- `src/order_guard/models/tables.py` — 新增 BusinessContext 表（或 KeyValue 表）
- `src/order_guard/engine/agent.py` — system prompt 注入 business_context
- `src/order_guard/engine/prompt.py` — prompt 构建适配
- `src/order_guard/api/feishu.py` — 意图分类增加 UPDATE_CONTEXT
- `config.example.yaml` — 添加 business_context 示例
- `alembic/versions/` — DB 迁移
- `tests/test_business_context.py` — 单元测试

## Tasks
- [ ] N8.1: Settings 新增 business_context 字段 + config.example.yaml 示例
- [ ] N8.2: Agent system prompt 注入 business_context
- [ ] N8.3: DB 模型 + 迁移（持久化对话更新的知识）
- [ ] N8.4: 飞书 Bot 意图分类增加 UPDATE_CONTEXT + 对话更新逻辑
- [ ] N8.5: 编写单元测试
