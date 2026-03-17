# T11: CSV/Excel 数据源 Connector

## Context
接入第一个真实数据源。支持从 CSV/Excel 文件读取订单、库存、销量数据。这是所有 ERP 都支持的通用导出格式，用户无需 API 对接即可使用。验证 Adapter 机制在真实数据下的可用性。

## Scope
### In Scope
- CSVConnector 实现 BaseConnector 接口
- 支持 CSV 和 Excel (.xlsx) 格式
- 字段映射配置（用户的列名 → 系统标准字段名）
- 自动检测文件编码（UTF-8, GBK 等）
- 支持配置多个文件（inventory_file, orders_file, sales_file）
- 数据校验（缺失字段提示、类型转换）
- 在 ConnectorRegistry 注册 "csv" 类型
- 附带一份示例 CSV 数据文件供测试

### Not In Scope
- 数据库数据源（MySQL/PostgreSQL Connector，后续单独做）
- 自动监听文件变化（热更新）
- Web UI 上传文件
- 数据清洗/预处理（超出基础类型转换的部分）

## Design

### 配置方式
```yaml
connectors:
  - name: "erp-export"
    type: "csv"
    enabled: true
    config:
      # 文件路径（支持相对路径和绝对路径）
      inventory_file: "data/inventory.csv"
      orders_file: "data/orders.csv"
      sales_file: "data/sales.csv"        # 可选

      # 字段映射（左边是 CSV 列名，右边是系统标准字段）
      # 如果列名和标准字段一致，可以不配
      field_mapping:
        inventory:
          "商品编码": "sku"
          "商品名称": "product_name"
          "当前库存": "current_stock"
          "日均销量": "daily_avg_sales"
          "仓库": "warehouse"
          "补货周期(天)": "reorder_lead_time_days"
        orders:
          "订单号": "order_id"
          "商品编码": "sku"
          "数量": "quantity"
          "退货数量": "returned_quantity"
          "订单日期": "order_date"
```

### 标准字段定义
```
# inventory 标准字段
sku: str                      # 必填
product_name: str             # 必填
current_stock: int            # 必填
daily_avg_sales: float        # 必填
warehouse: str                # 可选
category: str                 # 可选
reorder_lead_time_days: int   # 可选，默认 30
price: float                  # 可选
return_rate: float            # 可选

# orders 标准字段
order_id: str                 # 必填
sku: str                      # 必填
quantity: int                 # 必填
returned_quantity: int        # 可选，默认 0
order_date: str               # 必填
revenue: float                # 可选
product_name: str             # 可选

# sales 标准字段（聚合数据）
sku: str                      # 必填
total_quantity: int           # 必填
total_returned: int           # 可选
period_days: int              # 可选
```

### 自动推断逻辑
如果没有配置 field_mapping，系统尝试自动匹配：
1. 完全匹配标准字段名（如 CSV 列名就是 "sku"）
2. 忽略大小写匹配
3. 常见别名匹配（如 "SKU编码" → "sku"，"Stock" → "current_stock"）
4. 匹配失败的必填字段报错提示

### 数据流
```
CSV/Excel 文件
  → pandas 读取（自动检测编码）
  → 字段映射（配置 or 自动推断）
  → 类型转换（str→int, str→float）
  → 数据校验（必填字段检查）
  → 返回 list[dict]（和 MockConnector 格式一致）
```

### Key Decisions
- 使用 pandas 读取 CSV/Excel，成熟稳定，处理编码/格式问题
- 字段映射是核心设计——让系统适配用户的数据，而不是让用户改数据适配系统
- inventory 文件中如果没有 daily_avg_sales 但有 orders 文件，可以从 orders 中自动计算
- 示例数据文件用英文列名，减少编码问题

## Dependencies
- T01（项目骨架）
- T02（配置管理）— connectors 配置段
- T04（数据对接）— BaseConnector 接口和 ConnectorRegistry

## Tasks
- [ ] T11.1: 添加 pandas + openpyxl 依赖
- [ ] T11.2: 实现 CSVConnector 类（读取 CSV/Excel，字段映射，类型转换）
- [ ] T11.3: 实现字段映射逻辑（配置映射 + 自动推断 + 别名匹配）
- [ ] T11.4: 实现数据校验（必填字段、类型检查、错误提示）
- [ ] T11.5: 在 ConnectorRegistry 注册 "csv" 类型
- [ ] T11.6: 创建示例 CSV 数据文件（data/example/inventory.csv, orders.csv, sales.csv）
- [ ] T11.7: 编写单元测试
