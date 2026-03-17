# T07: 规则管理

## Context
管理业务检测规则。规则核心是自然语言 Prompt 模板，业务人员可编写和修改。系统加载规则后配合数据摘要发送给 LLM 做分析判断。

## Scope
### In Scope
- 规则数据模型（Prompt 模板 + 元信息）
- 从 YAML 配置加载预置规则
- 规则 CRUD（存储到数据库）
- 内置示例规则（库存检查、退货率检查）

### Not In Scope
- 规则版本管理（后续版本）
- Web UI 规则编辑（后续版本）
- 规则互相依赖/组合执行

## Design
### 规则结构
```yaml
rules:
  - id: "rule-inventory-risk"
    name: "库存风险检查"
    description: "检查 SKU 是否存在缺货或积压风险"
    connector: "mock"
    data_type: "inventory"
    prompt: |
      你是一个库存分析专家。请根据以下库存数据判断每个 SKU 的补货风险：

      判断规则：
      - 如果可售天数 < 补货周期，标记为 critical（紧急缺货风险）
      - 如果可售天数 < 补货周期 * 1.5，标记为 warning（需关注）
      - 如果可售天数 > 补货周期 * 5，标记为 warning（积压风险）
      - 其他情况标记为 info（正常）

      请结合商品特性给出补货建议。
    enabled: true

  - id: "rule-return-rate"
    name: "退货率异常检查"
    description: "检测退货率异常偏高的 SKU"
    connector: "mock"
    data_type: "orders"
    prompt: |
      你是一个电商运营分析专家。请分析以下订单数据中退货率异常的 SKU：

      判断规则：
      - 退货率 > 10% 标记为 critical
      - 退货率 > 5% 标记为 warning
      - 退货率 > 3% 标记为 info

      请分析可能的退货原因并给出改善建议。
    enabled: true
```

### Key Decisions
- 规则可以在 YAML 中预定义，也可以通过 API/数据库动态管理
- 系统启动时将 YAML 中的规则同步到数据库
- 每条规则关联一个 Connector 和数据类型，执行时知道从哪拉数据

## Dependencies
- T2（配置管理）— 规则定义在配置文件中
- T3（存储层）— 规则持久化到 alert_rules 表

## Tasks
- [ ] T7.1: 实现 RuleManager — 规则加载（YAML → DB 同步）
- [ ] T7.2: 实现规则 CRUD 函数（create / get / list / update / toggle）
- [ ] T7.3: 编写 2 条内置示例规则（库存风险 + 退货率检查）
- [ ] T7.4: 实现规则查找——按 ID 获取规则 + 关联的 Connector 信息
