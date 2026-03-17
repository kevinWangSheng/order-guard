# T04: 数据对接 — 验收标准

## Given/When/Then

- Given MockConnector 已注册, When 调用 health_check(), Then 返回 True
- Given MockConnector 已注册, When 调用 get_inventory(), Then 返回包含 SKU、库存量、日均销量等字段的列表
- Given MockConnector 已注册, When 调用 get_orders(date_range="7d"), Then 返回最近 7 天的模拟订单数据
- Given 配置文件中定义了 connector name="mock" type="mock", When 系统启动, Then ConnectorRegistry 能通过 get("mock") 获取实例
- Given 配置文件中定义了一个不存在的 connector type, When 系统启动, Then 抛出清晰错误提示

## Checklist

- [ ] BaseConnector 抽象类定义清晰，新数据源只需实现接口
- [ ] MockConnector 返回的数据结构贴近真实电商场景
- [ ] Mock 数据包含异常场景（至少：缺货、积压、高退货率各 1 个 SKU）
- [ ] ConnectorRegistry 支持按 name 查找
- [ ] 有单元测试覆盖 MockConnector 所有方法
