# T05: 指标计算 — 验收标准

## Given/When/Then

- Given 库存数据 {current_stock: 500, daily_avg_sales: 20}, When 计算 days_of_stock, Then 结果为 25
- Given days_of_stock=25 且 reorder_lead_time=45, When 计算 stock_risk, Then 标记为"缺货风险"
- Given days_of_stock=240 且 reorder_lead_time=45, When 计算 overstock_risk, Then 标记为"积压风险"
- Given 一组订单数据含 3 笔退货/100 笔总单, When 计算 return_rate, Then 结果为 0.03
- Given 一组计算好的指标, When 调用 SummaryBuilder.build(), Then 输出格式化的 Markdown 表格

## Checklist

- [ ] 所有指标计算有对应的单元测试
- [ ] 边界情况处理：日均销量为 0 时不除零报错
- [ ] 摘要输出为 Markdown 表格格式
- [ ] 指标计算不依赖任何 LLM 调用
- [ ] 支持处理空数据集（返回空摘要而非报错）
