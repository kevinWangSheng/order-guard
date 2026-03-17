# T16: MCP Client 基础层 — 验收标准

## 验收步骤

### 1. 依赖安装
```bash
uv sync
```
- [ ] mcp SDK 安装成功，无依赖冲突

### 2. 配置解析
- [ ] config.example.yaml 包含 mcp_servers 配置段和注释
- [ ] MCPServerConfig 正确解析 stdio 和 sse 两种传输配置
- [ ] 环境变量引用（${VAR}）在配置中正确替换

### 3. stdio 连接
```bash
# 用 SQLite MCP Server 测试
npx -y @modelcontextprotocol/server-sqlite test.db
```
- [ ] MCPConnection 能通过 stdio 启动并连接 MCP Server
- [ ] list_tools() 返回可用工具列表（如 list_tables, read_query 等）
- [ ] call_tool() 能调用工具并返回结果

### 4. 连接管理
- [ ] MCPManager.connect_all() 连接所有启用的 MCP Server
- [ ] MCPManager.disconnect_all() 正确关闭所有连接
- [ ] get_connection(name) 能按名称查找连接
- [ ] 连接失败时有清晰的错误日志，不影响其他 Server

### 5. FastAPI 集成
- [ ] FastAPI 启动时自动连接配置的 MCP Server
- [ ] FastAPI 关闭时自动断开所有连接

### 6. 工具信息格式
- [ ] ToolInfo 包含 name, description, input_schema, server_name
- [ ] input_schema 格式兼容 LLM function calling（JSON Schema）

### 7. 单元测试
```bash
uv run pytest tests/test_mcp_client.py -v
```
- [ ] 测试通过
