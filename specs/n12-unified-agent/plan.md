# N12: 统一 Agent 改造

## Context
v4 改版核心：去掉意图分类，改为单 Agent + 全量 12 个工具。本任务改造 Agent 层，包括：
1. 扩展工具集从 3 个到 12 个
2. 加写操作拦截（pending_action 确认机制）
3. 统一 system prompt

这是 v4 改版的枢纽任务，连接工具层（N9/N10/N11）和入口层（N13）。

## Scope

### In Scope
- Agent 支持 12 个工具的注册和调用
- 写操作拦截机制（代码层强制，不依赖 prompt）
- 统一 system prompt（身份 + 工具策略 + 对话策略 + 确认策略 + 业务知识注入 + 输出格式）
- data_tools.py — 从 DataAccessLayer 迁移 3 个数据查询工具到 tools 包

### Not In Scope
- 飞书入口改造（N13 负责）
- 工具函数实现（N9/N10/N11 已完成）
- 话题检测、记忆检索

## Design

### 工具注册

```python
# Agent 初始化时注册所有 12 个工具
ALL_TOOLS = [
    # 数据查询（3个）
    *data_tools.TOOL_DEFINITIONS,
    # 规则管理（5个）
    *rule_tools.TOOL_DEFINITIONS,
    # 业务知识（3个）
    *context_tools.TOOL_DEFINITIONS,
    # 告警历史（1个）
    *alert_tools.TOOL_DEFINITIONS,
]

agent = Agent(
    llm_client=llm_client,
    tools=ALL_TOOLS,
    tool_executors=ALL_EXECUTORS,  # name → function 映射
    config=AgentConfig(write_confirmation=True)
)
```

### 写操作拦截机制

在 Agent 的工具执行循环中，拦截写操作：

```python
WRITE_TOOLS = {"create_rule", "update_rule", "delete_rule", "delete_context"}

async def _execute_tool_call(self, tool_call) -> dict:
    if tool_call.name in WRITE_TOOLS and self.config.write_confirmation:
        # 不执行真实操作
        # 构建变更预览
        preview = self._build_preview(tool_call)

        # 存储 pending_action（Agent 返回给调用方）
        self.pending_action = {
            "tool_name": tool_call.name,
            "args": tool_call.arguments,
            "preview": preview,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(minutes=5)).isoformat()
        }

        # 返回伪结果给 LLM
        return {
            "data": {"status": "pending_confirmation", "preview": preview},
            "hint": "已向用户展示变更预览，请用自然语言告知用户变更内容并等待确认。"
        }

    # 非写操作，直接执行
    return await self.tool_executors[tool_call.name](**tool_call.arguments)
```

### 变更预览构建

```python
def _build_preview(self, tool_call) -> str:
    """根据工具和参数构建人类可读的变更预览"""
    if tool_call.name == "create_rule":
        args = tool_call.arguments
        return f"创建规则：{args['name']}\n数据源：{args['mcp_server']}\n执行频率：{args['schedule']}"

    elif tool_call.name == "update_rule":
        args = tool_call.arguments
        changes = args.get("changes", {})
        lines = [f"修改规则 ID={args['rule_id']}"]
        for k, v in changes.items():
            lines.append(f"  {k} → {v}")
        return "\n".join(lines)

    elif tool_call.name == "delete_rule":
        return f"删除规则 ID={tool_call.arguments['rule_id']}"

    elif tool_call.name == "delete_context":
        return f"删除业务知识 ID={tool_call.arguments['context_id']}"
```

### Agent 返回结构升级

```python
@dataclass
class AgentResult:
    response: str                    # LLM 的最终文字回复
    pending_action: dict | None      # 如果有写操作被拦截
    tool_calls_log: list[dict]       # 本次调用的工具记录（调试用）
```

### 统一 System Prompt

```python
UNIFIED_SYSTEM_PROMPT = """
你是 OrderGuard 数据助手，部署在企业飞书群中。

## 核心能力
- 连接企业数据库，查询和分析业务数据（库存、订单、销售、物流等）
- 创建和管理数据监控规则（定时检查 + 异常告警）
- 记录和管理业务知识（公司背景、促销、策略等）
- 查看历史告警记录

## 业务背景
{business_context}

## 工具调用策略
- 数据查询前：先 list_datasources 了解可用数据源，再 get_schema 了解表结构
- 创建规则前：先 list_datasources + get_schema 了解数据结构，确保 prompt_template 引用真实存在的表和字段
- 创建规则后：建议用 test_rule 试运行验证
- 修改/删除规则前：先 list_rules 确认目标规则

## 对话策略
- 区分提问和指令：用户问"能不能改"是提问，不要直接执行修改
- 信息不完整时追问，不要猜测填充参数（一次只问一个问题）
- 回复简洁专业，用中文

## 确认策略
- 写操作（创建/修改/删除规则、删除业务知识）会自动展示变更预览
- 收到预览后，用自然语言向用户描述即将执行的变更，等待用户确认

## 输出格式
- 数据分析结果用结构化格式（表格或列表）
- 告警信息标注严重程度
- 规则操作结果明确反馈成功/失败
"""
```

### data_tools.py 迁移

从 DataAccessLayer 迁移 3 个工具到 tools 包，保持接口不变：
```python
# tools/data_tools.py
# 复用 DataAccessLayer 的逻辑，封装为统一格式

def list_datasources() -> dict:
    """列出所有已连接的数据源"""

def get_schema(datasource_id: str, table_name: str | None = None) -> dict:
    """获取数据源的表结构"""

def query(datasource_id: str, sql: str) -> dict:
    """执行 SQL 查询（只允许 SELECT）"""
```

### Key Decisions
- 写操作拦截在 Agent 代码层强制执行，不依赖 LLM 遵守 prompt 指令
- pending_action 由 Agent 产生，由调用方（feishu.py）负责存储到 session
- Agent 不感知飞书/CLI 等具体入口，只返回 AgentResult
- 业务知识注入通过 build_context_injection()（N10）动态生成，拼接到 system prompt
- 12 个工具全量注入，~1500-2000 tokens，可接受

## Dependencies
- N9（规则工具集）— 5 个工具函数
- N10（业务知识工具集）— 3 个工具函数 + build_context_injection
- N11（告警历史工具）— 1 个工具函数
- N1（统一数据访问层）— DataAccessLayer 的 3 个工具

## File Changes
- `src/order_guard/engine/agent.py` — Agent 改造（工具注册 + 执行循环 + 写拦截 + AgentResult）
- `src/order_guard/tools/data_tools.py` — 3 个数据查询工具迁移
- `src/order_guard/engine/prompts.py` — 统一 system prompt 模板
- `tests/test_agent.py` — Agent 单元测试更新

## Tasks
- [ ] N12.1: data_tools.py — 迁移 3 个数据查询工具到 tools 包
- [ ] N12.2: Agent 工具注册机制（支持动态注册 12 个工具）
- [ ] N12.3: 写操作拦截 + pending_action 生成 + 变更预览构建
- [ ] N12.4: AgentResult 返回结构升级
- [ ] N12.5: 统一 system prompt（合并身份 + 策略 + 业务知识注入）
- [ ] N12.6: Agent 工具执行循环适配（统一返回信封处理）
- [ ] N12.7: 编写单元测试（重点测试写拦截逻辑）
