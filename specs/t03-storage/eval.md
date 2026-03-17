# T03: 存储层 — 验收标准

## Given/When/Then

- Given 系统首次启动, When 数据库文件不存在, Then 自动创建 SQLite 文件并执行迁移建表
- Given 数据库已初始化, When 调用 create_alert() 写入告警, Then 数据正确持久化并可通过 get_alert() 查询
- Given 数据库中有多条告警, When 调用 list_alerts(limit=10, offset=0), Then 返回分页结果
- Given Alembic 已配置, When 执行 `alembic upgrade head`, Then 所有表正确创建
- Given 已升级的数据库, When 执行 `alembic downgrade -1`, Then 回退一个版本无报错

## Checklist

- [ ] 4 个 SQLModel 模型可正常建表
- [ ] Alembic upgrade/downgrade 都能执行
- [ ] async session 正确管理（无连接泄漏）
- [ ] CRUD 函数覆盖 create / get / list / update
- [ ] JSON 字段正确序列化和反序列化
- [ ] 有单元测试覆盖核心 CRUD 操作
