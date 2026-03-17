# T08: 告警推送

## Context
将 AI 分析结果推送到外部渠道。MVP 阶段实现通用 Webhook 推送（POST JSON），后续扩展飞书/企业微信特定格式。

## Scope
### In Scope
- BaseAlertChannel 抽象接口
- WebhookChannel 实现（通用 HTTP POST JSON）
- 告警消息格式化（包含级别、标题、摘要、详情、时间、建议）
- 推送失败重试（最多 3 次）
- 推送结果记录到数据库（alerts 表 status 更新）
- 多渠道同时推送

### Not In Scope
- 飞书/企业微信特定消息格式（后续版本添加对应 Channel 实现）
- 告警静默期/去重（后续版本）
- 告警升级机制

## Design
### 接口定义
```python
class BaseAlertChannel(ABC):
    name: str
    type: str

    async def send(self, alert: AlertMessage) -> SendResult: ...
```

### AlertMessage 结构
```python
class AlertMessage:
    severity: str        # critical / warning / info
    title: str           # 告警标题
    summary: str         # 一句话摘要
    details: list[dict]  # 详细告警项
    suggestion: str      # 建议操作
    timestamp: datetime
    rule_name: str       # 触发规则名
    source: str          # 数据源名
```

### Webhook 请求体
```json
{
  "severity": "critical",
  "title": "库存风险告警",
  "summary": "3 个 SKU 存在缺货风险",
  "details": [...],
  "suggestion": "建议立即安排补货",
  "timestamp": "2026-03-07T09:00:00Z",
  "rule": "库存风险检查",
  "source": "mock"
}
```

### Key Decisions
- Adapter Pattern：和 Connector 一样，Channel 也是可插拔的
- 通用 Webhook 直接 POST JSON，飞书/企业微信后续只需实现各自的 Channel
- 重试使用指数退避（1s, 2s, 4s）

## Dependencies
- T3（存储层）— 推送记录存储
- T6（AI 层）— 接收分析结果作为告警内容

## Tasks
- [ ] T8.1: 定义 BaseAlertChannel 抽象类 + AlertMessage 数据类
- [ ] T8.2: 实现 WebhookChannel（httpx POST + 重试逻辑）
- [ ] T8.3: 实现 AlertDispatcher — 解析 AI 输出，生成 AlertMessage，分发到多渠道
- [ ] T8.4: 实现推送结果写入 alerts 表（status: sent / failed）
