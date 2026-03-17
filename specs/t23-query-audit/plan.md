# T23: 查询审计

## Context
AI Agent 自主查询数据库，需要记录所有执行的 SQL，用于安全审计、性能分析和问题排查。管理者需要知道 AI 查了什么数据、花了多长时间、返回了多少行。

## Scope

### In Scope
- query_logs 数据表（记录每条 AI 执行的 SQL）
- Agent 工具调用自动记录（拦截 execute_sql 调用）
- CLI 查看查询历史（`order-guard queries`）
- 异常查询检测（超时、大结果集、被拒绝的查询）
- 查询统计（API/CLI 展示）

### Not In Scope
- 实时告警（异常查询触发告警，后续版本）
- 查询重放/回溯
- 第三方审计系统对接

## Design

### query_logs 表
```python
class QueryLog(SQLModel, table=True):
    __tablename__ = "query_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    rule_id: str = Field(default="", index=True)     # 关联规则
    mcp_server: str = ""                               # 数据源名称
    sql: str = ""                                      # 执行的 SQL
    status: str = "success"                            # success / error / timeout / rejected
    rows_returned: int = 0                             # 返回行数
    duration_ms: int = 0                               # 耗时（毫秒）
    error: str | None = None                           # 错误信息
    agent_iteration: int = 0                           # Agent 第几轮循环
    created_at: datetime = Field(default_factory=_utcnow)
```

### Agent 工具调用拦截
```python
class AuditedAgent(Agent):
    """在 Agent 工具调用时自动记录查询日志"""

    async def _call_tool(self, tool_name: str, arguments: dict) -> Any:
        if tool_name == "execute_sql":
            start = time.monotonic()
            try:
                result = await super()._call_tool(tool_name, arguments)
                duration_ms = int((time.monotonic() - start) * 1000)
                await self._log_query(
                    sql=arguments.get("sql", ""),
                    status="success",
                    rows_returned=self._count_rows(result),
                    duration_ms=duration_ms,
                )
                return result
            except Exception as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                await self._log_query(
                    sql=arguments.get("sql", ""),
                    status="error",
                    error=str(e),
                    duration_ms=duration_ms,
                )
                raise
        return await super()._call_tool(tool_name, arguments)
```

### CLI 命令
```bash
# 查看最近 20 条查询
uv run order-guard queries --last 20

# 按规则过滤
uv run order-guard queries --rule rule-inventory-check

# 只看异常查询
uv run order-guard queries --status error,timeout,rejected

# 查询统计
uv run order-guard queries --stats
# 输出: 总查询数 | 成功率 | 平均耗时 | 平均返回行数
```

### Key Decisions
- 审计在 OrderGuard Agent 层实现（拦截工具调用），不依赖 DBHub
- 只记录 execute_sql 类工具调用，search_objects 等探索类不记录
- 查询日志保存到 OrderGuard 自己的数据库（不写入用户的业务库）
- CLI 提供查看和统计功能

## Dependencies
- T17（AI Agent）— Agent 工具调用拦截点
- T03（存储层）— 新增 query_logs 表

## File Changes
- `src/order_guard/models/tables.py` — 新增 QueryLog 模型
- `src/order_guard/engine/agent.py` — 工具调用审计拦截
- `src/order_guard/storage/crud.py` — query_logs CRUD
- `src/order_guard/cli.py` — queries 命令
- `alembic/versions/` — 迁移脚本
- `tests/test_query_audit.py` — 单元测试

## Tasks
- [ ] T23.1: 新增 QueryLog 模型 + DB 迁移
- [ ] T23.2: 实现 Agent 工具调用审计拦截
- [ ] T23.3: 实现 query_logs CRUD
- [ ] T23.4: CLI `queries` 命令（--last, --rule, --status, --stats）
- [ ] T23.5: 编写单元测试
