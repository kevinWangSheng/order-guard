# T04: 数据对接

## Context
定义数据源 Adapter 抽象接口，实现可插拔的数据源接入机制。MVP 阶段提供一个 Mock 数据源，模拟电商库存/订单数据，供后续模块开发和测试使用。

## Scope
### In Scope
- BaseConnector 抽象接口定义
- Mock 数据源实现（模拟库存、订单、销量数据）
- 数据源注册/发现机制
- Mock 数据要贴近真实电商场景（SKU、库存量、日均销量、退货率等）

### Not In Scope
- NetSuite 真实 API 接入（后续版本，按 Adapter 接口实现即可）
- 领星/其他 ERP 接入
- MCP 协议适配
- 数据缓存

## Design
### BaseConnector 接口
```python
class BaseConnector(ABC):
    name: str
    type: str

    async def health_check(self) -> bool: ...
    async def get_orders(self, filters: dict) -> list[dict]: ...
    async def get_inventory(self, filters: dict) -> list[dict]: ...
    async def get_sales(self, filters: dict) -> list[dict]: ...
    async def query(self, query_type: str, params: dict) -> list[dict]: ...
```

### Mock 数据示例
```json
{
  "sku": "SKU-A001",
  "product_name": "Wireless Earbuds Pro",
  "current_stock": 500,
  "warehouse": "US-West",
  "daily_avg_sales": 20,
  "return_rate": 0.03,
  "days_of_stock": 25,
  "reorder_lead_time_days": 45,
  "last_restock_date": "2026-02-15"
}
```

### Key Decisions
- Adapter Pattern：所有数据源实现 BaseConnector 接口
- Mock 数据源生成足够多样的数据（正常 + 异常场景），便于测试告警逻辑
- Connector 通过配置文件注册，运行时按 name 查找

## Dependencies
- T1（项目骨架）
- T2（配置管理）— connectors 配置段

## Tasks
- [ ] T4.1: 定义 BaseConnector 抽象类 + 数据类型
- [ ] T4.2: 实现 MockConnector（订单、库存、销量数据生成）
- [ ] T4.3: 实现 ConnectorRegistry（按配置注册和查找 Connector）
- [ ] T4.4: Mock 数据包含正常和异常场景（缺货、积压、高退货率）
