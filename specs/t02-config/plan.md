# T02: 配置管理

## Context
统一管理所有配置项：数据源连接、LLM 设置、推送渠道、定时任务等。支持 YAML 配置文件 + 环境变量覆盖，敏感信息不明文存储。

## Scope
### In Scope
- Pydantic Settings 配置类定义
- YAML 配置文件解析
- 环境变量自动覆盖（OG_ 前缀）
- config.example.yaml 模板（含所有配置项注释）
- 配置校验 + 清晰的错误提示
- 敏感信息支持环境变量引用

### Not In Scope
- 配置热重载（后续版本）
- Web UI 配置界面（后续版本）
- 配置加密存储

## Design
### 配置结构
```yaml
# config.yaml
app:
  name: "OrderGuard"
  debug: false
  log_level: "INFO"

llm:
  model: "gpt-4o"              # LiteLLM 格式，可切换
  api_key: "${OG_LLM_API_KEY}" # 环境变量引用
  max_tokens: 4096
  temperature: 0.1

database:
  url: "sqlite:///data/orderguard.db"

connectors:
  - name: "mock"
    type: "mock"
    enabled: true

alerts:
  channels:
    - name: "default-webhook"
      type: "webhook"
      url: "${OG_WEBHOOK_URL}"
      enabled: true

scheduler:
  jobs:
    - name: "daily-check"
      cron: "0 9 * * *"
      rule_ids: ["rule-001"]
      connector: "mock"
```

### Key Decisions
- 使用 Pydantic Settings v2，原生支持 YAML + env
- 环境变量前缀 OG_（OrderGuard），避免冲突
- 配置分层：app / llm / database / connectors / alerts / scheduler

## Dependencies
- T1（项目骨架）

## Tasks
- [ ] T2.1: 定义 Settings 类层级（AppSettings, LLMSettings, DBSettings 等）
- [ ] T2.2: 实现 YAML 文件加载 + 环境变量覆盖逻辑
- [ ] T2.3: 实现配置校验（必填项检查、格式校验、错误提示）
- [ ] T2.4: 创建 config.example.yaml（含详细注释）
- [ ] T2.5: 实现全局 get_settings() 单例
