# T16: MCP Client 基础层

## Context
OrderGuard 需要连接外部数据源（数据库、SaaS 平台等）。通过集成 MCP（Model Context Protocol）Client，系统可以连接任意 MCP Server，获取其提供的工具列表，供 AI Agent 调用。

## Scope

### In Scope
- MCP Python SDK 集成（`mcp` 包）
- 支持 stdio 和 SSE 两种传输方式
- MCP Server 连接管理（连接、断开、重连）
- 工具发现（列出 MCP Server 提供的所有工具）
- 工具调用（执行 MCP Server 的工具并获取结果）
- MCP Server 配置模型（YAML 配置 + 环境变量）
- 多 MCP Server 管理（MCPManager 统一管理所有连接）

### Not In Scope
- AI Agent 逻辑（T13 实现）
- Pipeline 集成（T14 实现）
- 资源（Resources）和提示（Prompts）协议（MVP 只用 Tools）

## Design

### 配置模型
```yaml
mcp_servers:
  - name: "warehouse-db"
    transport: "stdio"
    command: "npx"
    args: ["-y", "@modelcontextprotocol/server-sqlite", "/path/to/warehouse.db"]
    enabled: true

  - name: "erp-api"
    transport: "sse"
    url: "https://erp-mcp.company.com/sse"
    headers:
      Authorization: "Bearer ${ERP_API_KEY}"
    enabled: true
```

### 核心类设计

```python
# MCPServerConfig — 配置模型（Pydantic）
class MCPServerConfig(BaseModel):
    name: str
    transport: Literal["stdio", "sse"]
    command: str | None = None       # stdio 模式
    args: list[str] = []             # stdio 模式
    url: str | None = None           # sse 模式
    headers: dict[str, str] = {}     # sse 模式
    env: dict[str, str] = {}         # 环境变量
    enabled: bool = True

# MCPConnection — 单个 MCP Server 连接
class MCPConnection:
    async def connect() -> None
    async def disconnect() -> None
    async def list_tools() -> list[ToolInfo]
    async def call_tool(name: str, arguments: dict) -> Any
    def is_connected() -> bool

# MCPManager — 管理所有 MCP Server
class MCPManager:
    async def connect_all() -> None
    async def disconnect_all() -> None
    def get_connection(name: str) -> MCPConnection
    def list_connections() -> list[str]
    async def get_tools(server_name: str) -> list[ToolInfo]
```

### ToolInfo 格式（MCP 工具转 LLM function 定义）
```python
class ToolInfo(BaseModel):
    name: str                    # 工具名称，如 "read_query"
    description: str             # 工具描述
    input_schema: dict           # JSON Schema 参数定义
    server_name: str             # 所属 MCP Server
```

### Key Decisions
- 使用官方 `mcp` Python SDK，不自己实现协议
- stdio 模式：通过子进程启动 MCP Server（适合本地数据库）
- SSE 模式：通过 HTTP 连接远程 MCP Server（适合 SaaS API）
- 连接生命周期跟随 FastAPI lifespan（启动时连接，关闭时断开）
- 工具信息转换为 LLM function calling 格式，供 T13 使用

## Dependencies
- T02（配置管理）— MCP Server 配置段
- `mcp` Python SDK（新增依赖）

## File Changes
- `src/order_guard/mcp/__init__.py` — 新包
- `src/order_guard/mcp/client.py` — MCPConnection 实现
- `src/order_guard/mcp/manager.py` — MCPManager 实现
- `src/order_guard/mcp/models.py` — MCPServerConfig, ToolInfo
- `src/order_guard/config/settings.py` — 增加 mcp_servers 配置段
- `config.example.yaml` — 增加 mcp_servers 示例
- `pyproject.toml` — 增加 mcp 依赖
- `tests/test_mcp_client.py` — 单元测试

## Tasks
- [ ] T16.1: 添加 mcp SDK 依赖，创建 mcp 包目录
- [ ] T16.2: 实现 MCPServerConfig 配置模型 + Settings 集成
- [ ] T16.3: 实现 MCPConnection（stdio + SSE 两种传输）
- [ ] T16.4: 实现工具发现（list_tools）和工具调用（call_tool）
- [ ] T16.5: 实现 MCPManager 统一管理多连接
- [ ] T16.6: 集成 FastAPI lifespan（启动连接 / 关闭断开）
- [ ] T16.7: 更新 config.example.yaml 示例配置
- [ ] T16.8: 编写单元测试
