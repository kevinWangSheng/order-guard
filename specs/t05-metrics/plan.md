# T05: 指标计算

## Context
对数据源返回的原始数据做预处理，计算业务指标，生成结构化摘要供 LLM 分析。核心原则：**代码算数，LLM 判断**。

## Scope
### In Scope
- 指标计算引擎（接收原始数据，输出结构化摘要）
- 库存类指标：可售天数、库存周转率、缺货风险等级
- 订单类指标：退货率、日均订单量、异常大单检测
- 摘要生成：将指标格式化为 LLM 可理解的文本/表格

### Not In Scope
- 复杂统计分析（回归、预测）
- 同比环比计算（后续版本）
- 自定义指标公式（后续版本）

## Design
### 计算流程
```
原始数据（from Connector）
  → MetricsEngine.compute(data, metric_type)
  → 计算结果（结构化 dict）
  → SummaryBuilder.build(metrics)
  → 文本摘要（给 LLM 的输入）
```

### 核心指标
| 指标 | 计算方式 | 说明 |
|------|---------|------|
| days_of_stock | current_stock / daily_avg_sales | 库存可售天数 |
| stock_risk | days_of_stock vs reorder_lead_time | 缺货风险 |
| overstock_risk | days_of_stock > lead_time * 5 | 积压风险 |
| return_rate | returns / total_orders | 退货率 |

### 摘要输出示例
```
库存分析摘要（2026-03-07）

| SKU | 商品 | 库存 | 日均销量 | 可售天数 | 补货周期 | 风险 |
|-----|------|------|---------|---------|---------|------|
| SKU-A001 | Wireless Earbuds | 500 | 20 | 25天 | 45天(海运) | 缺货风险 |
| SKU-B002 | Phone Case | 1200 | 5 | 240天 | 45天(海运) | 积压 |
| SKU-C003 | USB Cable | 80 | 15 | 5天 | 7天(空运) | 正常 |
```

### Key Decisions
- 指标计算是纯 Python 代码，不依赖 LLM，保证计算准确性
- 摘要格式化为 Markdown 表格，LLM 解析友好
- 计算逻辑和摘要生成分离，方便后续扩展指标类型

## Dependencies
- T4（数据对接）— 需要 Connector 返回的原始数据

## Tasks
- [ ] T5.1: 定义 MetricsEngine 类 + 指标计算函数
- [ ] T5.2: 实现库存类指标计算（days_of_stock, stock_risk, overstock_risk）
- [ ] T5.3: 实现订单类指标计算（return_rate, order_volume）
- [ ] T5.4: 实现 SummaryBuilder — 将指标格式化为 Markdown 摘要
