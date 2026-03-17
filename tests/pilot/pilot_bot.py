"""TestPilot — 本地自动化测试机器人.

通过 HTTP 接口模拟飞书用户消息，走完整 OrderGuard 生产代码路径，
收集回复、AI 评判、定时推送报告。

不需要额外的飞书 Bot 应用。直接 POST 到 localhost:8000/api/feishu/event。

Usage:
    uv run order-guard pilot start          # 启动（每小时一轮）
    uv run order-guard pilot start --once   # 只跑一轮全部场景
    uv run order-guard pilot list           # 列出所有角色/场景
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml
from loguru import logger


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class PilotConfig:
    orderguard_url: str = "http://localhost:8000"
    llm_model: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    mode: str = "sequential"          # sequential | random
    interval_seconds: int = 3600      # 每轮间隔（默认 1 小时）
    max_turns_per_scenario: int = 5   # 每个场景最多对话轮数
    reply_timeout_seconds: int = 60   # 等待回复超时
    total_rounds: int = 0             # 总轮数，0=无限
    report_every_n: int = 0           # 每 N 个场景出报告，0=每轮结束出
    personas_file: str = ""
    feishu_webhook_url: str = ""      # 报告推送 webhook


def load_pilot_config(config_path: Path | None = None) -> PilotConfig:
    """Load pilot config, with env var substitution."""
    if config_path is None:
        config_path = Path(__file__).parent / "config.yaml"

    if not config_path.exists():
        return PilotConfig()

    with open(config_path, "r", encoding="utf-8") as f:
        raw = f.read()

    import re
    raw = re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), raw)
    data = yaml.safe_load(raw) or {}
    strategy = data.get("strategy", {})

    return PilotConfig(
        orderguard_url=data.get("orderguard_url", "http://localhost:8000"),
        llm_model=data.get("llm", {}).get("model", ""),
        llm_api_key=data.get("llm", {}).get("api_key", ""),
        llm_api_base=data.get("llm", {}).get("api_base", ""),
        mode=strategy.get("mode", "sequential"),
        interval_seconds=strategy.get("interval_seconds", 3600),
        max_turns_per_scenario=strategy.get("max_turns_per_scenario", 5),
        reply_timeout_seconds=strategy.get("reply_timeout_seconds", 60),
        total_rounds=strategy.get("total_rounds", 0),
        report_every_n=strategy.get("report_every_n", 0),
        personas_file=data.get("personas_file", ""),
        feishu_webhook_url=data.get("feishu_webhook_url", ""),
    )


# ---------------------------------------------------------------------------
# Scenario result
# ---------------------------------------------------------------------------

@dataclass
class ConversationRecord:
    role_name: str
    persona_name: str
    task_name: str
    task_id: str
    success: bool = False
    messages: list[dict] = field(default_factory=list)
    criteria: list[str] = field(default_factory=list)
    passed_criteria: list[str] = field(default_factory=list)
    failed_criteria: list[str] = field(default_factory=list)
    reasoning: str = ""
    response_times: list[float] = field(default_factory=list)
    error: str = ""
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------------------
# Reply collection — hook into OrderGuard's _reply_text
# ---------------------------------------------------------------------------

_reply_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
_original_reply_text = None


def _install_reply_hook():
    """Monkey-patch _reply_text to capture replies into a queue."""
    global _original_reply_text
    from order_guard.api import feishu

    if _original_reply_text is not None:
        return  # Already installed

    _original_reply_text = feishu._reply_text

    async def _hooked_reply_text(bot_config, chat_id, text, message_id=""):
        # Put reply into queue for TestPilot to consume
        await _reply_queue.put((chat_id, text))
        # Still call original so logs etc. work (will fail on Feishu API, that's fine)
        try:
            await _original_reply_text(bot_config, chat_id, text, message_id)
        except Exception:
            pass  # Feishu API call will fail locally, expected

    feishu._reply_text = _hooked_reply_text


def _uninstall_reply_hook():
    """Restore original _reply_text."""
    global _original_reply_text
    if _original_reply_text is not None:
        from order_guard.api import feishu
        feishu._reply_text = _original_reply_text
        _original_reply_text = None


# ---------------------------------------------------------------------------
# Send simulated Feishu event
# ---------------------------------------------------------------------------

async def _send_feishu_event(
    base_url: str,
    user_id: str,
    chat_id: str,
    text: str,
) -> bool:
    """POST a simulated Feishu message event to OrderGuard."""
    event_id = f"pilot_{uuid.uuid4().hex[:16]}"
    message_id = f"om_pilot_{uuid.uuid4().hex[:12]}"

    payload = {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "create_time": str(int(time.time() * 1000)),
            "token": "pilot_test",
            "app_id": "pilot",
            "tenant_key": "pilot",
        },
        "event": {
            "sender": {
                "sender_id": {
                    "user_id": user_id,
                    "open_id": user_id,
                    "union_id": user_id,
                },
                "sender_type": "user",
                "tenant_key": "pilot",
            },
            "message": {
                "message_id": message_id,
                "chat_id": chat_id,
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": text}),
                "mentions": [],
                "create_time": str(int(time.time() * 1000)),
            },
        },
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{base_url}/api/feishu/event", json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.error("Failed to send event to OrderGuard: {}", e)
        return False


async def _collect_reply(chat_id: str, timeout: float) -> str | None:
    """Wait for OrderGuard's reply from the hook queue."""
    deadline = time.time() + timeout
    collected_parts = []

    while time.time() < deadline:
        try:
            remaining = max(0.1, deadline - time.time())
            cid, text = await asyncio.wait_for(_reply_queue.get(), timeout=remaining)
            if cid == chat_id:
                collected_parts.append(text)
                # Give a short window for additional parts (progressive output)
                await asyncio.sleep(1.0)
                # Drain any remaining
                while not _reply_queue.empty():
                    try:
                        cid2, text2 = _reply_queue.get_nowait()
                        if cid2 == chat_id:
                            collected_parts.append(text2)
                    except asyncio.QueueEmpty:
                        break
                break
        except asyncio.TimeoutError:
            break

    return "\n".join(collected_parts) if collected_parts else None


# ---------------------------------------------------------------------------
# AI helpers
# ---------------------------------------------------------------------------

async def _llm_call(messages: list[dict], model: str, api_key: str, api_base: str = "") -> str:
    import litellm
    resp = await litellm.acompletion(
        model=model, messages=messages,
        temperature=0.7, max_tokens=500,
        api_key=api_key, api_base=api_base or None,
    )
    return resp.choices[0].message.content or ""


async def generate_user_message(
    persona_prompt: str, history: list[dict],
    model: str, api_key: str, api_base: str = "",
) -> str:
    """AI generates the next user message based on persona."""
    messages = [
        {"role": "system", "content": persona_prompt},
        *history,
        {"role": "user", "content": "请生成你的下一条消息。只输出消息内容本身。"},
    ]
    return await _llm_call(messages, model, api_key, api_base)


async def evaluate_conversation(
    conversation: list[dict], criteria: list[str],
    model: str, api_key: str, api_base: str = "",
) -> dict:
    """AI evaluates conversation against criteria."""
    criteria_text = "\n".join(f"- {c}" for c in criteria)
    conv_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else 'Agent'}: {m['content'][:500]}"
        for m in conversation
    )
    messages = [
        {"role": "system", "content":
            "你是 AI 系统测试评审员。评估对话是否满足标准。\n"
            "JSON 格式回复：{\"passed\": [...], \"failed\": [...], \"reasoning\": \"...\"}"},
        {"role": "user", "content": f"## 标准\n{criteria_text}\n\n## 对话\n{conv_text}"},
    ]
    raw = await _llm_call(messages, model, api_key, api_base)
    try:
        if "```" in raw:
            raw = raw.split("```json")[-1].split("```")[0] if "```json" in raw else raw.split("```")[1].split("```")[0]
        result = json.loads(raw.strip())
        return {
            "success": len(result.get("failed", [])) == 0,
            "passed": result.get("passed", []),
            "failed": result.get("failed", []),
            "reasoning": result.get("reasoning", ""),
        }
    except (json.JSONDecodeError, IndexError):
        return {"success": False, "passed": [], "failed": criteria, "reasoning": f"解析失败: {raw[:200]}"}


# ---------------------------------------------------------------------------
# Load personas
# ---------------------------------------------------------------------------

def load_tasks(config: PilotConfig) -> list[dict]:
    """Load persona tasks from YAML."""
    yaml_path = Path(config.personas_file) if config.personas_file else None
    if not yaml_path or not yaml_path.exists():
        yaml_path = Path(__file__).parent.parent / "scenarios" / "personas.yaml"
    if not yaml_path.exists():
        raise FileNotFoundError(f"personas.yaml not found: {yaml_path}")

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tasks = []
    for role in data.get("roles", []):
        for persona in role.get("personas", []):
            for task in role.get("tasks", []):
                tasks.append({
                    "role_id": role["id"],
                    "role_name": role["name"],
                    "role_description": role["description"],
                    "persona_id": persona["id"],
                    "persona_name": persona["name"],
                    "persona_traits": persona["traits"],
                    "task_id": task["id"],
                    "task_name": task["name"],
                    "task_goal": task["goal"],
                    "criteria": task["criteria"],
                })
    return tasks


# ---------------------------------------------------------------------------
# Run one scenario
# ---------------------------------------------------------------------------

async def run_scenario(
    config: PilotConfig, task: dict,
    llm_model: str, llm_key: str, llm_base: str,
) -> ConversationRecord:
    """Run one multi-turn conversation through the real OrderGuard HTTP endpoint."""
    record = ConversationRecord(
        role_name=task["role_name"],
        persona_name=task["persona_name"],
        task_name=task["task_name"],
        task_id=task["task_id"],
        criteria=task["criteria"],
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    # Unique IDs for this scenario — simulates a unique Feishu user + chat
    user_id = f"pilot_{task['role_id']}_{task['persona_id']}"
    chat_id = f"pilot_chat_{task['role_id']}_{task['persona_id']}_{uuid.uuid4().hex[:8]}"

    persona_prompt = (
        f"你扮演「{task['persona_name']}」，一位{task['role_name']}。\n"
        f"背景：{task['role_description']}\n"
        f"性格：{task['persona_traits']}\n"
        f"任务：{task['task_name']}\n"
        f"目标：{task['task_goal']}\n\n"
        f"用中文自然沟通，像真实用户一样逐步提问，不要一次说完。"
    )

    conversation: list[dict] = []

    try:
        for turn in range(config.max_turns_per_scenario):
            # 1. AI generates user message
            user_msg = await generate_user_message(
                persona_prompt, conversation, llm_model, llm_key, llm_base,
            )
            if not user_msg.strip():
                break

            conversation.append({"role": "user", "content": user_msg})
            record.messages.append({
                "role": "user", "content": user_msg,
                "ts": datetime.now(timezone.utc).isoformat(),
            })
            logger.info("[{}] 用户: {}", task["persona_name"], user_msg[:80])

            # 2. Send to OrderGuard via HTTP
            # Clear queue first
            while not _reply_queue.empty():
                try:
                    _reply_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

            t0 = time.time()
            ok = await _send_feishu_event(config.orderguard_url, user_id, chat_id, user_msg)
            if not ok:
                record.error = f"第 {turn + 1} 轮: 发送失败"
                break

            # 3. Collect reply
            reply = await _collect_reply(chat_id, config.reply_timeout_seconds)
            elapsed = time.time() - t0
            record.response_times.append(elapsed)

            if reply is None:
                record.error = f"第 {turn + 1} 轮: 超时 ({config.reply_timeout_seconds}s)"
                logger.warning(record.error)
                break

            conversation.append({"role": "assistant", "content": reply})
            record.messages.append({
                "role": "assistant", "content": reply,
                "ts": datetime.now(timezone.utc).isoformat(),
                "response_time": round(elapsed, 1),
            })
            logger.info("[{}] Agent ({:.1f}s): {}", task["persona_name"], elapsed, reply[:80])

            # 4. Continue if Agent asked a question and we haven't reached max turns
            if turn >= config.max_turns_per_scenario - 1:
                break
            if "？" not in reply and "?" not in reply and turn >= 1:
                break

        # 5. Evaluate
        if conversation:
            ev = await evaluate_conversation(conversation, task["criteria"], llm_model, llm_key, llm_base)
            record.success = ev["success"]
            record.passed_criteria = ev["passed"]
            record.failed_criteria = ev["failed"]
            record.reasoning = ev["reasoning"]

    except Exception as e:
        record.error = str(e)
        logger.error("Scenario error: {}", e)

    record.finished_at = datetime.now(timezone.utc).isoformat()
    return record


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report_card(records: list[ConversationRecord]) -> dict:
    """Build Feishu card for test results."""
    passed = sum(1 for r in records if r.success)
    total = len(records)
    all_rts = [rt for r in records for rt in r.response_times]
    avg_rt = sum(all_rts) / len(all_rts) if all_rts else 0

    header_color = "green" if passed == total else ("orange" if passed > total // 2 else "red")

    lines = ["| 角色 | 人设 | 场景 | 结果 | 响应 |",
             "|------|------|------|------|------|"]
    for r in records:
        status = "✅" if r.success else "❌"
        avg = sum(r.response_times) / len(r.response_times) if r.response_times else 0
        lines.append(f"| {r.role_name} | {r.persona_name} | {r.task_name} | {status} | {avg:.1f}s |")

    failed_parts = []
    for r in records:
        if not r.success:
            failed_parts.append(f"**{r.persona_name} — {r.task_name}**")
            for c in r.failed_criteria:
                failed_parts.append(f"  - ❌ {c}")
            if r.error:
                failed_parts.append(f"  - 错误: {r.error[:100]}")

    elements = [
        {"tag": "markdown", "content": f"**{passed}/{total} 通过** | 平均响应 {avg_rt:.1f}s"},
        {"tag": "hr"},
        {"tag": "markdown", "content": "\n".join(lines)},
    ]
    if failed_parts:
        elements += [{"tag": "hr"}, {"tag": "markdown", "content": "\n".join(failed_parts)}]
    elements += [{"tag": "hr"}, {"tag": "note", "elements": [
        {"tag": "plain_text", "content": f"TestPilot | {datetime.now().strftime('%Y-%m-%d %H:%M')}"}
    ]}]

    return {
        "header": {"title": {"tag": "plain_text", "content": f"TestPilot — {passed}/{total} 通过"}, "template": header_color},
        "elements": elements,
    }


async def push_report(records: list[ConversationRecord], webhook_url: str):
    """Push report card to Feishu webhook."""
    if not webhook_url:
        return
    card = build_report_card(records)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook_url, json={"msg_type": "interactive", "card": card})
        logger.info("Report pushed to Feishu")
    except Exception as e:
        logger.error("Report push failed: {}", e)


def save_report(records: list[ConversationRecord]) -> Path:
    """Save JSON report to disk."""
    report_dir = Path(__file__).parent / "reports"
    report_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / f"pilot_{ts}.json"

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": len(records),
        "passed": sum(1 for r in records if r.success),
        "records": [
            {
                "role": r.role_name, "persona": r.persona_name,
                "task": r.task_name, "success": r.success,
                "passed_criteria": r.passed_criteria,
                "failed_criteria": r.failed_criteria,
                "reasoning": r.reasoning,
                "response_times": r.response_times,
                "messages": r.messages,
                "error": r.error,
                "started_at": r.started_at, "finished_at": r.finished_at,
            }
            for r in records
        ],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ---------------------------------------------------------------------------
# Resolve LLM config
# ---------------------------------------------------------------------------

def resolve_llm(config: PilotConfig) -> tuple[str, str, str]:
    """Get LLM model/key/base from pilot config or OrderGuard settings."""
    model, key, base = config.llm_model, config.llm_api_key, config.llm_api_base
    if not model or not key:
        try:
            from order_guard.config import get_settings
            s = get_settings()
            model = model or s.llm.model
            key = key or (s.llm.api_key.get_secret_value() if s.llm.api_key else "")
            base = base or s.llm.api_base or ""
        except Exception:
            pass
    if not model or not key:
        raise ValueError("LLM 未配置")
    return model, key, base


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run_pilot(
    config: PilotConfig,
    once: bool = False,
    role_filter: str | None = None,
    task_filter: str | None = None,
):
    """Main TestPilot loop.

    Runs inside the same process as OrderGuard (called from CLI after server starts),
    or as a standalone script pointing at a running OrderGuard instance.
    """
    llm_model, llm_key, llm_base = resolve_llm(config)
    tasks = load_tasks(config)

    if role_filter:
        tasks = [t for t in tasks if role_filter in (t["role_id"], t["role_name"])]
    if task_filter:
        tasks = [t for t in tasks if task_filter in (t["task_id"], t["task_name"])]
    if not tasks:
        logger.error("No matching scenarios")
        return []

    _install_reply_hook()
    logger.info("TestPilot: {} scenarios, mode={}, interval={}s",
                len(tasks), config.mode, config.interval_seconds)

    all_records: list[ConversationRecord] = []
    round_count = 0

    try:
        while True:
            round_count += 1
            round_tasks = list(tasks)
            if config.mode == "random":
                random.shuffle(round_tasks)

            logger.info("=== Round {} ({} scenarios) ===", round_count, len(round_tasks))

            round_records = []
            for i, task in enumerate(round_tasks):
                label = f"{task['persona_name']}/{task['task_name']}"
                logger.info("[{}/{}] {}", i + 1, len(round_tasks), label)

                record = await run_scenario(config, task, llm_model, llm_key, llm_base)
                round_records.append(record)
                all_records.append(record)

                status = "✅" if record.success else "❌"
                logger.info("  {} ({:.1f}s avg)", status,
                            sum(record.response_times) / len(record.response_times) if record.response_times else 0)

            # Round report
            passed = sum(1 for r in round_records if r.success)
            logger.info("Round {} done: {}/{} passed", round_count, passed, len(round_records))

            path = save_report(round_records)
            logger.info("Report: {}", path)

            if config.feishu_webhook_url:
                await push_report(round_records, config.feishu_webhook_url)

            if once:
                break
            if config.total_rounds > 0 and round_count >= config.total_rounds:
                break

            logger.info("Next round in {}s...", config.interval_seconds)
            await asyncio.sleep(config.interval_seconds)

    except KeyboardInterrupt:
        logger.info("TestPilot stopped")
    finally:
        _uninstall_reply_hook()

    return all_records
