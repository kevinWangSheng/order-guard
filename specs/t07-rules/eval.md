# T07: 规则管理 — 验收标准

## Given/When/Then

- Given config.yaml 中定义了 2 条规则, When 系统启动, Then 规则被同步到 alert_rules 表
- Given 数据库中已有规则, When 调用 get_rule("rule-inventory-risk"), Then 返回完整规则信息含 prompt_template
- Given 一条规则 enabled=true, When 调用 toggle_rule("rule-xxx", false), Then 规则变为禁用状态
- Given 新的规则定义, When 调用 create_rule(), Then 规则存入数据库
- Given YAML 中规则 prompt 被修改, When 系统重启, Then 数据库中的规则同步更新

## Checklist

- [ ] YAML 规则加载 + DB 同步逻辑正确
- [ ] 2 条内置示例规则可正常加载
- [ ] CRUD 函数工作正常
- [ ] 规则关联 connector 信息可查
- [ ] 有单元测试覆盖规则加载和 CRUD
