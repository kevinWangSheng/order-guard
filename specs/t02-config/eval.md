# T02: 配置管理 — 验收标准

## Given/When/Then

- Given config.example.yaml 存在, When 复制为 config.yaml 并填入必要值, Then 系统启动时正确加载所有配置项
- Given config.yaml 中 llm.api_key 为 "${OG_LLM_API_KEY}", When 设置环境变量 OG_LLM_API_KEY=sk-xxx, Then 配置解析后 api_key 值为 "sk-xxx"
- Given config.yaml 中 llm.model 为 "gpt-4o", When 同时设置环境变量 OG_LLM__MODEL=claude-sonnet, Then 最终值为 "claude-sonnet"（环境变量优先）
- Given config.yaml 缺少必填项（如 llm.model）, When 系统启动, Then 抛出清晰的校验错误提示，说明缺少哪个字段
- Given config.yaml 格式错误（如无效 YAML 语法）, When 系统启动, Then 抛出解析错误并指示错误位置

## Checklist

- [ ] config.example.yaml 包含所有配置项 + 中英文注释
- [ ] Pydantic Settings 类型校验正常工作
- [ ] 环境变量覆盖机制正确（OG_ 前缀）
- [ ] 敏感信息不出现在日志输出中
- [ ] get_settings() 返回全局单例
- [ ] 有单元测试覆盖正常/异常配置场景
