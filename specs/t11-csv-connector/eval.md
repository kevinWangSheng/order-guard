# T11: CSV/Excel 数据源 Connector — 验收标准

## Given/When/Then

- Given 配置了 csv connector 且 inventory.csv 存在, When 调用 get_inventory(), Then 返回正确的库存数据列表
- Given CSV 列名是中文（"商品编码","当前库存"）, When 配置了 field_mapping, Then 正确映射为标准字段名（sku, current_stock）
- Given CSV 列名就是标准字段名（sku, current_stock）, When 没有配置 field_mapping, Then 自动匹配成功
- Given Excel (.xlsx) 文件, When 调用 get_inventory(), Then 正确读取和 CSV 一样的结果
- Given CSV 文件编码是 GBK, When 读取文件, Then 自动检测编码并正确解析中文
- Given CSV 缺少必填字段（如没有 sku 列）, When 读取文件, Then 抛出清晰错误提示，说明缺少哪个字段
- Given 配置了 orders_file, When 调用 get_orders(), Then 返回订单数据列表
- Given inventory 和 orders 数据都存在, When 通过 run_detection_job 执行, Then 端到端走通（读CSV → 指标计算 → LLM 分析 → 告警推送）
- Given config.yaml 中 connector type="csv", When 系统启动, Then ConnectorRegistry 正确注册 CSVConnector
- Given health_check(), When inventory_file 存在, Then 返回 True
- Given health_check(), When inventory_file 不存在, Then 返回 False

## Checklist

- [ ] CSVConnector 实现 BaseConnector 全部方法（health_check, get_inventory, get_orders, get_sales）
- [ ] 支持 CSV 和 Excel (.xlsx) 两种格式
- [ ] 字段映射支持配置和自动推断
- [ ] 自动检测文件编码
- [ ] 必填字段缺失时有清晰错误提示
- [ ] ConnectorRegistry 中注册了 "csv" 类型
- [ ] 示例 CSV 文件（至少 inventory + orders）已创建
- [ ] 端到端测试：CSV 数据 → 指标计算 → LLM 分析 → 告警（dry-run）
- [ ] 有单元测试
- [ ] pyproject.toml 已添加 pandas + openpyxl 依赖
