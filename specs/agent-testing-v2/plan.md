# Agent 生产级测试方案 v2

## 背景与问题

当前 L3 场景测试（`tests/scenarios/`）存在三个根本性缺陷：

1. **预设脚本**：`personas.yaml` 用固定消息序列，AI 不真正"对话"
2. **criteria 太浅**：只检查"有没有调用工具"，不验证答案是否正确
3. **单轮孤立**：每个场景是独立对话，不测试多轮上下文连贯性和长时间运行

目标：测试 Agent 在**模拟真实运营人员日常使用**场景下的正确性、连贯性和长时间稳定性，通过 LangWatch trace 驱动持续优化。

---

## 核心设计原则

```
旧：[预设消息1] → [回答1] → [预设消息2] → [回答2] → 检查关键词
新：[业务目标 + 人设 + 行为指南] → AI 自由追问 → 会话级评分 → 5维度判断
```

---

## 任务清单

### E5: 人设体系 + Investigation 场景定义

**新文件**：`tests/scenarios/scenarios_v2.yaml`

**人设矩阵**（4个人设，覆盖真实用户分布）：

| 人设 | 专业度 | 表达 | 追问深度 | 对抗性 | 代表场景 |
|------|--------|------|----------|--------|---------|
| 小王（菜鸡运营） | 低 | 模糊 | 低 | 无 | "最近怎样" |
| 李姐（老运营） | 中 | 正常 | 高 | 中 | 追问数字细节 |
| 陈（数据分析师） | 高 | 精确 | 高 | 高 | 查 SQL 逻辑 |
| 王总（管理层） | 无 | 极简 | 无 | 低 | "有问题吗" |

**5个 Investigation 场景**（goal-driven，不是 Q&A-driven）：

| 场景 | 人设 | 轮数 | 核心能力测试 |
|------|------|------|-------------|
| S01 库存缺货调查 | 小王 + 李姐 | 15-20 | 多步推理、数据准确性 |
| S02 退货异常诊断 | 李姐 + 陈 | 15 | 跨表关联、因果分析 |
| S03 月度经营复盘 | 王总 | 12 | 话题切换处理 |
| S04 分析师深度追问 | 陈 | 20 | SQL 透明度、逻辑一致性 |
| S05 规则配置全流程 | 李姐 | 12 | 写拦截、确认机制 |

**场景 YAML 格式**（替换旧的 messages 格式）：

```yaml
scenarios:
  - id: S01
    title: 库存缺货风险调查
    personas: [xiao_wang, li_jie]  # 同一场景跑2个人设
    max_turns: 20

    # 业务背景 — 注入 UserSimulatorAgent 的 system_prompt
    business_context: |
      你发现最近几个热卖品销量突然上涨，担心库存跟不上。
      你要弄清楚哪些商品有缺货风险，严重程度，能撑几天，以及有没有在途补货。

    # 行为指南 — 控制 AI 用户的追问路径
    conversation_guidelines:
      - 先问一个宽泛问题，不要一开始就说"缺货"
      - 当 agent 返回数据后，追问最糟糕的那一个 SKU
      - 要求 agent 估算"按现在销量能撑多少天"
      - 如果 agent 提到补货计划，追问"预计什么时候到"

    # Ground truth — 用于 session scorer 验证数据准确性
    ground_truth:
      must_identify:           # Agent 必须识别出的 SKU
        - sku: SKU-001
          condition: "库存为0，已缺货"
        - sku: SKU-003
          condition: "库存低于安全线"
        - sku: SKU-005
          condition: "库存低于安全线"
      critical_sku: SKU-001    # 最严重的，Agent 必须重点提及

    # 会话级通过标准（5维度，全部 True 才算 PASS）
    session_criteria:
      goal_achieved: "用户在对话结束时清楚地知道了哪些 SKU 有缺货风险及严重程度"
      data_accuracy: "Agent 提及的 SKU 编号和库存状态与 ground_truth 一致，无捏造数据"
      actionable: "Agent 提供了至少一条具体可操作建议（补货/调拨/设告警）"
      no_hallucination: "Agent 没有编造 ground_truth 以外的 SKU 或不存在的数字"
      conversation_quality: "对话连贯，Agent 正确处理了追问，没有重复废话或答非所问"
```

**Ground truth 数据库**（`tests/scenarios/ground_truth_db.py`）：

受控 SQLite，植入确定性数据，与 ground_truth YAML 对应。不使用真实 MySQL/PG（避免 ground truth 随生产数据变化而失效）。

---

### E6: Session Scorer（5维度 LLM-as-Judge）

**新文件**：`tests/scenarios/session_scorer.py`

**评估对象**：整个会话轨迹（不是单轮回复）

**5个维度**（每个 0/1，全部 1 才 PASS）：

```python
class SessionScore:
    goal_achieved: bool        # 用户的核心目标是否实现？
    data_accuracy: bool        # 数字/SKU 是否与 ground_truth 一致？
    actionable: bool           # 是否给出了可操作建议，不是空话？
    no_hallucination: bool     # 是否没有编造数据或 SKU？
    conversation_quality: bool # 对话是否连贯，追问是否被正确处理？
```

**Judge prompt 设计**：

```
你是一个专业的 AI Agent 评估员。
给定以下对话记录和评估标准，对每个维度给出 PASS/FAIL 和原因。

[对话记录]
{conversation}

[评估标准]
goal_achieved: {goal_achieved_criterion}
data_accuracy: Ground truth 数据: {ground_truth_json}
...

请严格按 JSON 格式返回：
{"goal_achieved": {"pass": true/false, "reason": "..."}, ...}
```

**与 LangWatch 集成**：每次评估后 push scores 到 LangWatch，与现有 pilot_bot 的 Langfuse push 方式一致。

---

### E7: Soak Test 框架

**新文件**：`tests/scenarios/soak_runner.py`

**目的**：检测 Agent 的稳定性——同一个场景多次运行，pass rate 应该 ≥ 85%。

**运行方式**：

```bash
# 对 S01 场景跑 20 次，统计通过率分布
uv run pytest tests/scenarios/ -m soak --scenario S01 --rounds 20

# 对所有场景各跑 10 次
uv run pytest tests/scenarios/ -m soak --rounds 10
```

**输出统计**：

```
场景          通过率    平均耗时    Token均值    失败原因TOP3
S01 库存调查   85%      42s        3200         data_accuracy(2), actionable(1)
S02 退货诊断   70%      38s        2800         goal_achieved(4), no_hallucination(2)
...
```

通过率 < 85% → 红色告警，需要优先优化。

---

### E8: 时序一致性测试

**新文件**：`tests/scenarios/temporal_coherence_test.py`

**目的**：检测 Agent 在 20-30 轮长对话中，前期答案和后期答案是否自相矛盾。

**测试设计**：

```python
# 时序探针：在指定轮次植入探测问题
probes = [
    {"turn": 4,  "question": "SKU-001 现在库存是多少？", "probe_id": "stockout_probe"},
    {"turn": 18, "question": "SKU-001 的库存情况有变化吗？", "probe_id": "stockout_probe"},
]

# 评估：第18轮的答案是否与第4轮一致（同一数据，无新查询则不变）
```

**场景脚本**（30轮对话框架）：

```
轮1-5:   库存缺货调查
轮6-10:  话题切换到退货率
轮11-15: 话题切换到销售数据
轮16-20: 话题切换到规则配置
轮21-25: 切回库存（植入探针，验证第4轮的答案）
轮26-30: 要求总结今日操作
```

**通过标准**：

- 探针答案一致性 ≥ 90%（允许 10% 因模型随机性导致的细微措辞差异）
- Agent 在没有重新查询的情况下，不得给出不同的数字
- Agent 在上下文截断后，不得凭空捏造之前提到的内容

---

## 文件结构变更

```
tests/scenarios/
├── conftest.py                  # 改造：接入 ground_truth_db，移除 in-memory 业务数据
├── personas.yaml                # 保留（L3 现有测试继续用）
├── scenarios_v2.yaml            # 新建：5个 investigation 场景定义（E5）
├── ground_truth_db.py           # 新建：受控 SQLite + 植入数据（E5）
├── session_scorer.py            # 新建：5维度 LLM-as-Judge（E6）
├── soak_runner.py               # 新建：Soak test 框架（E7）
├── temporal_coherence_test.py   # 新建：时序一致性测试（E8）
├── test_investigation.py        # 新建：5个场景 × 2人设 = 10个 pytest case（E5+E6）
├── test_soak.py                 # 新建：Soak test entry（E7）
├── test_temporal.py             # 新建：时序一致性 entry（E8）
│
│   # 保留不动
├── seed_data.py
├── persona_runner.py
├── test_tool_registration.py
├── test_chat_query_e2e.py
├── test_detection_e2e.py
├── test_full_pipeline_e2e.py
├── test_lifecycle_e2e.py
└── test_rule_crud_e2e.py
```

---

## 执行命令

```bash
# E5+E6: Investigation 场景测试（需要 LLM API Key）
uv run pytest tests/scenarios/test_investigation.py -v -m e2e

# 只跑某个场景
uv run pytest tests/scenarios/test_investigation.py -k "S01" -v

# 只跑某个人设
uv run pytest tests/scenarios/test_investigation.py -k "xiao_wang" -v

# E7: Soak test（20轮，约20-40分钟）
uv run pytest tests/scenarios/test_soak.py -v -m soak

# E8: 时序一致性（约30分钟）
uv run pytest tests/scenarios/test_temporal.py -v -m e2e

# 全部（不含 soak，日常 CI 用）
uv run pytest tests/scenarios/ -m "e2e and not soak" -v

# 完整质量报告（含 Feishu 推送）
uv run order-guard test-scenarios --push-feishu
```

---

## 验收标准

| 指标 | 合格线 | 优秀线 |
|------|--------|--------|
| Investigation 场景通过率 | ≥ 70% | ≥ 90% |
| Soak test pass rate（每场景） | ≥ 85% | ≥ 95% |
| 时序一致性 | ≥ 90% | ≥ 98% |
| 平均响应时间 | ≤ 60s/轮 | ≤ 30s/轮 |
| 幻觉率（no_hallucination） | 0% | 0% |

---

## 实施顺序

```
E5（场景 + 数据层）→ E6（评估层）→ E5+E6 集成测试 → E7（Soak）→ E8（时序）
```

每步完成后先跑通至少1个场景，确认流程正确再扩展。

---

## 依赖

- LLM API Key（已配置）
- LangWatch API Key（已配置）
- `langwatch-scenario` 包（已安装）
- 飞书 Webhook（已配置，报告推送用）
