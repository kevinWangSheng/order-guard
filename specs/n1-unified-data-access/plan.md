# N1: 统一数据访问层

## Context
当前架构中，每个 MCP Server 的工具（execute_sql、search_objects 等）直接暴露给 Agent。当客户接入多个数据源时，Agent 看到的工具数量线性增长，导致 LLM 选择困难、上下文膨胀。

本任务将所有数据源访问抽象为固定工具集，Agent 始终只看到 3 个数据查询工具，内部根据 datasource_id 路由到对应的 Adapter。

## Scope

### In Scope
- 定义统一工具集：`list_datasources` / `get_schema` / `query`
- 实现统一路由层（DataAccessLayer），根据 datasource 类型分发
- SQL Adapter（复用 DBHub/MCP 现有能力）
- MCP Adapter（转发到客户已有的 MCP Server）
- 适配现有 Agent 循环，替换当前直接暴露 MCP 工具的方式
- 适配现有 Scheduler pipeline
- 保持现有规则和飞书 Bot 正常工作

### Not In Scope
- API Adapter（后续需要时再加，接口预留）
- 跨数据源 JOIN
- 数据缓存

## Design

### 架构位置
```
Agent 调用固定工具 → DataAccessLayer → 路由到 Adapter → 具体执行
                                         ├── SQLAdapter (DBHub MCP)
                                         ├── MCPAdapter (任意 MCP Server)
                                         └── (预留 APIAdapter)
```

### 统一工具定义
```python
# 工具 1: 列出所有数据源
def list_datasources() -> list[DataSourceInfo]:
    """返回所有已配置的数据源及其基本信息"""
    # DataSourceInfo: { id, name, type, description, tables_count }

# 工具 2: 查看数据源 Schema
def get_schema(datasource_id: str, table_name: str | None = None) -> SchemaInfo:
    """获取某数据源的表结构/字段信息
    - 不传 table_name: 返回所有表的列表
    - 传 table_name: 返回该表的字段详情（名称、类型、注释、外键）
    """

# 工具 3: 查询数据
def query(datasource_id: str, sql: str) -> QueryResult:
    """对指定数据源执行 SQL 查询
    - SQL Adapter: 直接通过 DBHub execute_sql
    - MCP Adapter: 转发到对应 MCP Server 的查询工具
    """
```

### DataAccessLayer 核心类
```python
class DataAccessLayer:
    """统一数据访问层，管理所有数据源的 Adapter"""

    def __init__(self, mcp_manager: MCPManager, settings: Settings):
        self._adapters: dict[str, BaseAdapter] = {}
        # 根据 settings.mcp_servers 初始化 Adapter

    async def initialize(self):
        """启动时初始化所有 Adapter"""

    def get_tools(self) -> list[ToolInfo]:
        """返回固定的 3 个工具定义（list_datasources / get_schema / query）"""

    async def call_tool(self, tool_name: str, args: dict) -> str:
        """路由工具调用到对应 Adapter"""

    async def list_datasources(self) -> list[dict]:
        """列出所有数据源"""

    async def get_schema(self, datasource_id: str, table_name: str | None) -> dict:
        """获取 Schema"""

    async def query(self, datasource_id: str, sql: str) -> dict:
        """执行查询"""
```

### BaseAdapter 接口
```python
class BaseAdapter(ABC):
    """数据源 Adapter 基类"""

    @abstractmethod
    async def get_schema(self, table_name: str | None = None) -> dict: ...

    @abstractmethod
    async def query(self, sql: str) -> dict: ...

    @abstractmethod
    async def test_connection(self) -> bool: ...

    @property
    @abstractmethod
    def datasource_info(self) -> DataSourceInfo: ...
```

### SQLAdapter（复用 DBHub）
```python
class SQLAdapter(BaseAdapter):
    """通过 DBHub MCP 访问 SQL 数据库"""

    def __init__(self, mcp_connection: MCPConnection, config: MCPServerConfig):
        self._mcp = mcp_connection
        self._config = config

    async def get_schema(self, table_name=None) -> dict:
        # 调用 MCP 的 search_objects / describe_table
        ...

    async def query(self, sql: str) -> dict:
        # 调用 MCP 的 execute_sql
        # 自动应用 security 约束（readonly / max_rows / timeout）
        ...
```

### MCPAdapter（通用 MCP Server）
```python
class MCPAdapter(BaseAdapter):
    """通过任意 MCP Server 访问数据"""

    def __init__(self, mcp_connection: MCPConnection, config: MCPServerConfig):
        self._mcp = mcp_connection
        self._config = config

    async def get_schema(self, table_name=None) -> dict:
        # 尝试调用 MCP Server 的 list_tools 获取可用工具列表
        # 作为 "schema" 返回
        ...

    async def query(self, sql: str) -> dict:
        # 对于通用 MCP Server，"sql" 参数实际上可能是自然语言
        # 路由到最合适的 MCP tool
        ...
```

### Agent 适配
```python
# 修改 engine/agent.py

# Before (v3):
# tools = await self._mcp.list_tools()  # 直接暴露 MCP 工具

# After (v4):
# tools = self._data_access.get_tools()  # 固定 3 个工具
# Agent 调用 tool → DataAccessLayer.call_tool() → 路由到 Adapter
```

### 配置复用
不需要新的配置格式。现有的 `mcp_servers` 配置直接映射为数据源：
- `type: "dbhub"` → SQLAdapter
- `type: "generic"` → MCPAdapter
- `name` → datasource_id

### Key Decisions
- 工具数量固定为 3 个（list_datasources / get_schema / query），不随数据源增长
- 复用现有 MCPManager 和 MCPConnection，不重复建设
- SQLAdapter 底层仍通过 DBHub MCP 执行，安全特性（readonly/timeout/max_rows）保持不变
- Schema 加载（T21）、SQL 校验（T21）、查询审计（T23）在 DataAccessLayer 中统一处理
- API Adapter 接口预留，不在本任务实现

## Dependencies
- T16-T20（MCP 基础已完成）
- T21（Schema 加载——需要适配新的统一层）
- T23（查询审计——需要适配新的统一层）

## File Changes
- `src/order_guard/data_access/__init__.py` — 新包
- `src/order_guard/data_access/layer.py` — DataAccessLayer 核心
- `src/order_guard/data_access/base.py` — BaseAdapter 抽象
- `src/order_guard/data_access/sql_adapter.py` — SQLAdapter
- `src/order_guard/data_access/mcp_adapter.py` — MCPAdapter
- `src/order_guard/data_access/models.py` — DataSourceInfo / SchemaInfo / QueryResult
- `src/order_guard/engine/agent.py` — 适配 DataAccessLayer（替换直接 MCP 工具暴露）
- `src/order_guard/scheduler/jobs.py` — 适配 DataAccessLayer
- `src/order_guard/api/feishu.py` — 适配 DataAccessLayer
- `src/order_guard/main.py` — DataAccessLayer 初始化
- `tests/test_data_access.py` — 单元测试

## Tasks
- [ ] N1.1: 定义 BaseAdapter 抽象接口 + DataSourceInfo / SchemaInfo / QueryResult 模型
- [ ] N1.2: 实现 DataAccessLayer 核心（工具定义 + 路由 + 生命周期管理）
- [ ] N1.3: 实现 SQLAdapter（复用 DBHub MCP）
- [ ] N1.4: 实现 MCPAdapter（通用 MCP Server 转发）
- [ ] N1.5: 适配 Agent（替换直接 MCP 工具暴露为 DataAccessLayer.get_tools()）
- [ ] N1.6: 适配 Scheduler pipeline
- [ ] N1.7: 适配飞书 Bot 对话
- [ ] N1.8: 适配 Schema 加载和查询审计
- [ ] N1.9: 端到端测试（现有规则 + 飞书 Bot 全链路回归）
- [ ] N1.10: 编写单元测试
