# T03: 存储层

## Context
持久化系统运行数据：告警历史、任务执行日志、规则配置、数据源配置。默认 SQLite，后续可切换 PostgreSQL。

## Scope
### In Scope
- SQLModel 模型定义（alerts, alert_rules, task_runs, data_sources）
- SQLite 数据库初始化
- Alembic 迁移配置 + 初始迁移
- DB session 管理（async）
- 基础 CRUD 工具函数

### Not In Scope
- PostgreSQL 适配测试（后续版本，代码层面 SQLModel 天然支持）
- 数据归档/清理策略
- 对话记录表（后续 Bot 接入时添加）

## Design
### Data Model

#### alerts 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| rule_id | str | 关联规则 ID |
| severity | str | critical / warning / info |
| title | str | 告警标题 |
| summary | str | 告警摘要 |
| details | JSON | 告警详情（LLM 输出） |
| status | str | pending / sent / failed |
| created_at | datetime | 创建时间 |
| sent_at | datetime | 推送时间 |

#### alert_rules 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | str | 规则 ID（如 rule-001） |
| name | str | 规则名称 |
| description | str | 规则描述 |
| prompt_template | text | Prompt 模板 |
| connector_id | str | 关联数据源 |
| enabled | bool | 是否启用 |
| created_at | datetime | 创建时间 |
| updated_at | datetime | 更新时间 |

#### task_runs 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | UUID | 主键 |
| job_name | str | 任务名称 |
| rule_id | str | 关联规则 |
| status | str | running / success / failed |
| started_at | datetime | 开始时间 |
| completed_at | datetime | 完成时间 |
| duration_ms | int | 耗时（毫秒） |
| error | text | 错误信息 |
| result_summary | JSON | 执行结果摘要 |

#### data_sources 表
| 字段 | 类型 | 说明 |
|------|------|------|
| id | str | 数据源 ID |
| name | str | 名称 |
| type | str | mock / netsuite / rest |
| config | JSON | 连接配置（脱敏） |
| enabled | bool | 是否启用 |
| created_at | datetime | 创建时间 |

### Key Decisions
- 使用 SQLModel 而非 raw SQLAlchemy，与 FastAPI 天然集成
- JSON 字段存储灵活数据（告警详情、执行结果、连接配置）
- UUID 主键（alerts, task_runs），字符串主键（alert_rules, data_sources）

## Dependencies
- T1（项目骨架）
- T2（配置管理）— 需要 DB_URL 配置

## Tasks
- [ ] T3.1: 定义 SQLModel 模型（alerts, alert_rules, task_runs, data_sources）
- [ ] T3.2: 实现 database.py — 引擎初始化 + async session 管理
- [ ] T3.3: 配置 Alembic + 生成初始迁移
- [ ] T3.4: 实现基础 CRUD 函数（create/get/list/update）
