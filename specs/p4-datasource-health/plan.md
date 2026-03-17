# P4: 数据源健康监控

## Context
当前数据源（DBHub / MCP Server）如果断连或挂掉，系统是沉默失败的：
- 定时巡检会在 TaskRun 记录 error，但用户不主动看就不知道
- 飞书 Bot 查询时才会报错，但错误信息不够友好
- 没有主动告知机制

数据源不可用 → 巡检失败 → 告警缺失 → 用户以为"没有异常" → 实际上根本没检测。这是最危险的故障模式。

## Scope

### In Scope
- 定时健康检查 Job（APScheduler，可配置间隔）
- `DataSourceHealthLog` 新表记录探活历史
- 连续失败 N 次自动推送告警（飞书/企微）
- 1 个新 Agent 工具：`check_health`
- 对话场景覆盖：查看状态、手动探活、查看历史

### Not In Scope
- 自动故障恢复（自动重连 / 自动切换备用数据源）
- 数据源性能监控（查询延迟、QPS）
- 数据源容量监控（磁盘/内存）

## Design

### 数据模型

```python
class DataSourceHealthLog(SQLModel, table=True):
    __tablename__ = "datasource_health_logs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    datasource_id: str = Field(index=True)   # MCP Server name
    status: str = ""                          # "healthy" / "unhealthy" / "timeout"
    latency_ms: int = 0                      # 探活耗时
    error: str | None = None                 # 失败时的错误信息
    tool_count: int = 0                      # 可用工具数量
    created_at: datetime = Field(default_factory=_utcnow)
```

### 健康检查逻辑

```python
async def check_datasource_health(datasource_id: str) -> HealthResult:
    """对单个数据源执行健康检查"""
    # 1. 通过 DataAccessLayer 获取对应 adapter
    # 2. 调用 adapter.test_connection() 或 adapter.get_info()
    # 3. 记录结果到 DataSourceHealthLog
    # 4. 返回 HealthResult
```

test_connection 的实现：
- **SQL Adapter**：执行 `SELECT 1`（通过 DBHub MCP 的 execute_sql）
- **MCP Adapter**：调用 `list_tools()`，确认有响应

超时：默认 10 秒，可配置。

### 定时健康检查 Job

```python
# scheduler/jobs.py
async def _health_check_job(mcp_manager, dal):
    """定时健康检查所有数据源"""
    for ds in dal.list_datasources():
        result = await check_datasource_health(ds.id)
        if result.status == "unhealthy":
            # 检查连续失败次数
            consecutive_failures = await _get_consecutive_failures(ds.id)
            if consecutive_failures >= settings.health_check_alert_threshold:
                await _push_health_alert(ds, consecutive_failures, result.error)
```

### 配置

settings.py 新增：
```python
class HealthCheckConfig(BaseModel):
    enabled: bool = True
    interval_minutes: int = 5          # 检查间隔
    timeout_seconds: int = 10          # 单次检查超时
    alert_threshold: int = 3           # 连续失败 N 次告警
    retention_hours: int = 72          # 健康日志保留时间
```

config.example.yaml:
```yaml
health_check:
  enabled: true
  interval_minutes: 5
  timeout_seconds: 10
  alert_threshold: 3       # 连续失败3次推送告警
  retention_hours: 72      # 日志保留72小时
```

### 健康告警推送

当连续失败达到阈值时，通过 AlertDispatcher 推送：
```
🔴 数据源连接异常

数据源：erp_mysql
状态：连续 3 次探活失败
最近错误：Connection refused (localhost:3306)
上次正常：2026-03-12 14:30:00

影响范围：
- 缺货检测（每天 9:00）
- 退货异常（每 2 小时）
共 2 条规则依赖此数据源。

建议：检查数据库服务是否正常运行。
```

恢复后自动推送恢复通知：
```
🟢 数据源已恢复

数据源：erp_mysql
状态：连接正常（延迟 15ms）
故障持续：25 分钟（14:30 - 14:55）
```

### 工具定义

#### check_health
```python
async def check_health(
    datasource_id: str | None = None    # 不传则检查所有
) -> dict:
    """检查数据源健康状态"""
    # 返回：
    # 单个：{ datasource_id, status, latency_ms, last_check, uptime_24h }
    # 全部：[{ datasource_id, status, latency_ms, last_check, uptime_24h }, ...]
```

Tool Schema:
```json
{
  "name": "check_health",
  "description": "检查数据源连接健康状态。不传参数则检查所有数据源。返回连接状态、延迟、最近 24 小时可用率。如果发现异常，会提示受影响的规则。",
  "input_schema": {
    "type": "object",
    "properties": {
      "datasource_id": {
        "type": "string",
        "description": "数据源 ID，从 list_datasources 获取。不传则检查所有数据源"
      }
    },
    "required": []
  }
}
```

### 对话场景示例
```
用户：数据源连接正常吗
Agent：调用 check_health()
Agent：3 个数据源全部正常。erp_mysql 延迟 12ms，pg_analytics 延迟 8ms，warehouse 延迟 5ms。

用户：erp_mysql 最近有掉线吗
Agent：调用 check_health(datasource_id="erp_mysql")
Agent：erp_mysql 当前正常（延迟 12ms）。最近 24 小时可用率 99.2%，今天 14:30 有一次短暂断连（持续 5 分钟）。
```

## Dependencies
- N1（统一数据访问层）— DataAccessLayer 提供数据源列表和 adapter
- T09（调度层）— 注册定时健康检查 Job
- T08（告警推送）— 通过 AlertDispatcher 推送健康告警

## File Changes
- `src/order_guard/models/tables.py` — 新增 DataSourceHealthLog 模型
- `src/order_guard/tools/health_tools.py` — 新建，check_health 工具
- `src/order_guard/scheduler/jobs.py` — 新增 _health_check_job
- `src/order_guard/scheduler/setup.py` — 注册健康检查 Job
- `src/order_guard/config/settings.py` — 新增 HealthCheckConfig
- `src/order_guard/engine/agent.py` — 注册 check_health 工具
- `alembic/versions/xxx_add_datasource_health_logs.py` — 迁移脚本
- `tests/test_health_tools.py` — 单元测试

## Tasks
- [ ] P4.1: DataSourceHealthLog 模型 + Alembic 迁移
- [ ] P4.2: HealthCheckConfig 配置 + config.example.yaml
- [ ] P4.3: 实现健康检查核心逻辑（test_connection + 超时 + 记录）
- [ ] P4.4: 定时健康检查 Job（APScheduler 注册）
- [ ] P4.5: 连续失败告警推送（含受影响规则列表）
- [ ] P4.6: 恢复通知推送
- [ ] P4.7: 实现 check_health 工具（单个 + 全部 + 24h 可用率）
- [ ] P4.8: 注册到统一 Agent 工具集
- [ ] P4.9: 健康日志自动清理（retention_hours）
- [ ] P4.10: 编写单元测试
- [ ] P4.11: 全量回归测试
