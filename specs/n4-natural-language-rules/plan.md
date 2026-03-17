# N4: 自然语言配规则

## Context
当前规则通过手写 YAML 配置，需要了解 prompt 模板语法、MCP Server 名称、数据库表结构等技术细节。运营人员无法自行配置。

本任务让用户通过自然语言对话创建监控规则：说一句话描述需求 → LLM 理解并生成结构化规则 → 用户确认 → 规则生效。

## Scope

### In Scope
- 用户通过对话描述监控需求
- LLM 调用 get_schema 了解数据结构后生成规则
- 规则预览 + 用户确认机制
- 确认后写入 DB，纳入定时调度
- 通过对话修改、启用/禁用、删除已有规则
- 飞书 Bot + CLI 两个入口

### Not In Scope
- 规则版本管理
- 规则模板市场
- 可视化规则编辑器（Web UI）
- 规则测试运行（"先跑一次看看效果"——可作为后续功能）

## Design

### 核心流程

```
用户："帮我监控日销量下降超过30%的SKU，每天早上9点检查"
         │
         ▼
意图识别：CREATE_RULE（非 QUERY / CHAT / UPDATE_CONTEXT）
         │
         ▼
LLM 调用 list_datasources() → 知道有哪些数据源
LLM 调用 get_schema(datasource_id) → 知道表结构和字段
         │
         ▼
LLM 生成结构化规则：
{
  "name": "SKU 销量骤降监控",
  "description": "监控日销量较7日均值下降超过30%的SKU",
  "mcp_server": "erp-mysql",
  "prompt_template": "请查询 daily_sales_summary 表...",
  "schedule": "0 9 * * *",
  "data_window": "7d",
  "severity_default": "high",
  "enabled": true
}
         │
         ▼
格式化预览发给用户：
"📋 规则预览：
 名称：SKU 销量骤降监控
 数据源：erp-mysql
 检查频率：每天 9:00
 条件：日销量较7日均值下降 > 30%
 告警级别：高
 确认创建？"
         │
         ▼
用户确认 → RuleManager.create_rule() → 写入 DB → 纳入调度
```

### 意图分类扩展
在飞书 Bot 现有意图分类中增加规则相关的意图：

```python
# 现有意图：QUERY / CHAT
# 新增意图：CREATE_RULE / MANAGE_RULE / UPDATE_CONTEXT

INTENT_CLASSIFY_PROMPT = """
判断用户消息的意图类型：
- QUERY: 查询数据、分析数据（如"上周销量怎么样"）
- CHAT: 闲聊、打招呼、问问题（如"你好"、"你能做什么"）
- CREATE_RULE: 创建或设置监控规则（如"帮我监控..."、"设一个告警..."）
- MANAGE_RULE: 管理已有规则（如"看看现在有什么规则"、"关掉那个库存告警"）
- UPDATE_CONTEXT: 更新业务知识（如"记住，我们下周促销"）

用户消息：{message}
意图类型：
"""
```

### 规则生成 Prompt
```python
RULE_GENERATION_PROMPT = """
你是一个监控规则配置助手。用户想创建一个数据监控规则。

## 可用数据源
{datasources_info}

## 数据源 Schema
{schema_info}

## 用户需求
{user_message}

## 要求
1. 根据用户需求和数据结构，生成一条监控规则
2. prompt_template 要包含具体的 SQL 查询逻辑
3. schedule 用 cron 表达式
4. data_window 根据查询需要设定

请输出 JSON 格式的规则定义：
{
  "name": "规则名称",
  "description": "规则描述",
  "mcp_server": "数据源ID",
  "prompt_template": "完整的分析 prompt（包含 SQL 查询指令）",
  "schedule": "cron 表达式",
  "data_window": "时间窗口如 7d",
  "enabled": true
}
"""
```

### 规则管理对话
```
用户："看看现在有什么规则"
→ MANAGE_RULE → RuleManager.list_rules() → 格式化列表返回

用户："关掉库存那个告警"
→ MANAGE_RULE → LLM 匹配规则名 → RuleManager.update_rule(enabled=False)

用户："把销量监控的阈值改成20%"
→ MANAGE_RULE → LLM 修改 prompt_template → 预览 → 确认 → 更新

用户："删掉那个退货率的规则"
→ MANAGE_RULE → 确认 → RuleManager.delete_rule()
```

### 确认机制
所有规则变更操作都需要用户确认：
- 创建规则：展示预览 → 等待确认
- 修改规则：展示变更对比 → 等待确认
- 删除规则：二次确认

确认状态存在 SessionManager 中（当前会话的 pending_action）：
```python
# 在 Session 中保存待确认操作
pending_action = {
    "type": "create_rule",
    "data": { ... },  # 规则内容
    "expires_at": datetime.utcnow() + timedelta(minutes=5)
}
```

用户回复"确认"/"是"/"好的" → 执行 pending_action → 清除状态

### 调度集成
规则创建后需要纳入 APScheduler：
```python
# 创建规则后，动态添加调度任务
scheduler.add_job(
    run_detection_job,
    CronTrigger.from_crontab(rule.schedule),
    id=f"rule-{rule.id}",
    kwargs={"rule_id": rule.id, ...}
)
```

现有 scheduler setup 在启动时从 DB 加载所有 enabled 规则。新创建的规则需要动态注册，不需要重启。

### Key Decisions
- 规则的 prompt_template 由 LLM 生成，包含具体的 SQL 查询逻辑
- 确认机制是必须的（防止 LLM 理解错误直接创建规则）
- 确认超时 5 分钟自动取消
- 规则修改也走"预览 → 确认"流程
- 动态注册调度任务，无需重启

## Dependencies
- N1（统一数据访问层）— LLM 需要 list_datasources / get_schema 工具了解数据结构
- N5（会话管理）— 确认状态存在 session 中

## File Changes
- `src/order_guard/engine/rule_agent.py` — 规则生成/管理的 LLM 逻辑（新文件）
- `src/order_guard/engine/prompts/` — 规则相关 prompt 模板（新目录或扩展 prompt.py）
- `src/order_guard/api/feishu.py` — 意图分类扩展 + 规则对话处理
- `src/order_guard/engine/rules.py` — 扩展 CRUD（动态创建、删除）
- `src/order_guard/scheduler/setup.py` — 动态注册/注销调度任务
- `src/order_guard/cli.py` — rules create 命令（CLI 入口）
- `tests/test_rule_agent.py` — 单元测试

## Tasks
- [ ] N4.1: 意图分类扩展（CREATE_RULE / MANAGE_RULE）
- [ ] N4.2: 规则生成 Agent（调用 get_schema → 生成结构化规则 JSON）
- [ ] N4.3: 规则预览 + 确认机制（pending_action + 超时）
- [ ] N4.4: 确认后写入 DB + 动态注册调度任务
- [ ] N4.5: 规则管理对话（查看/修改/启用/禁用/删除）
- [ ] N4.6: CLI rules create 命令
- [ ] N4.7: 编写单元测试
