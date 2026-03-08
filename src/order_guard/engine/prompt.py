"""Prompt builder for LLM analysis."""

from __future__ import annotations

SYSTEM_PROMPT = """你是 OrderGuard 的 AI 分析引擎，负责分析电商库存和订单数据，识别异常并给出建议。

分析原则：
1. 基于提供的数据摘要和业务规则进行判断
2. 只关注确实存在风险的项目，不要过度告警
3. 给出的建议应当具体、可执行
4. 严格按照要求的 JSON 格式输出

输出要求：
- alerts: 告警列表，每条包含 severity（critical/warning/info）、title、reason、suggestion
- summary: 一段整体分析总结（中文）
- has_alerts: 是否有需要关注的告警（布尔值）

如果数据一切正常没有异常，返回空 alerts 列表，has_alerts 为 false。"""

OUTPUT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "analysis_result",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "alerts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "sku": {"type": "string"},
                            "severity": {"type": "string", "enum": ["critical", "warning", "info"]},
                            "title": {"type": "string"},
                            "reason": {"type": "string"},
                            "suggestion": {"type": "string"},
                        },
                        "required": ["severity", "title", "reason", "suggestion", "sku"],
                        "additionalProperties": False,
                    },
                },
                "summary": {"type": "string"},
                "has_alerts": {"type": "boolean"},
            },
            "required": ["alerts", "summary", "has_alerts"],
            "additionalProperties": False,
        },
    },
}


class PromptBuilder:
    """Assemble prompts for LLM analysis."""

    def build_messages(
        self,
        data_summary: str,
        rule_prompt: str,
    ) -> list[dict[str, str]]:
        """Build the messages array for LLM completion."""
        user_content = f"""## 业务规则
{rule_prompt}

## 数据摘要
{data_summary}

请根据以上业务规则分析数据摘要中的异常情况，输出 JSON 格式的分析结果。"""

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
