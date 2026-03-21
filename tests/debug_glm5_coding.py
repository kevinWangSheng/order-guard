"""验证 GLM-5 Coding Plan 纯对话返回空的问题。

运行: uv run python tests/debug_glm5_coding.py
"""
import asyncio
import json
import litellm

API_KEY = "cc0c9a8fbff5457f96f76919a8f40097.NpkI9NAFv4ndU5bv"
API_BASE = "https://open.bigmodel.cn/api/coding/paas/v4"
MODEL = "openai/glm-5"

DUMMY_TOOL = [{
    "type": "function",
    "function": {
        "name": "noop",
        "description": "空操作",
        "parameters": {"type": "object", "properties": {"x": {"type": "string"}}},
    },
}]

WEATHER_TOOL = [{
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "查询指定城市的实时天气信息，返回温度、湿度、天气状况等",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "城市名称，如：北京、上海、深圳",
                },
            },
            "required": ["city"],
        },
    },
}]


async def call(name: str, *, tools=None, response_format=None):
    kwargs = dict(
        model=MODEL,
        messages=[
            {"role": "user", "content": "你好，今天深圳天气怎么样？用一句话回复。"},
        ],
        temperature=0.5,
        max_tokens=100,
        api_key=API_KEY,
        api_base=API_BASE,
    )
    if tools is not None:
        kwargs["tools"] = tools
    if response_format is not None:
        kwargs["response_format"] = response_format

    try:
        resp = await litellm.acompletion(**kwargs)
        print(f"12312312{resp}")
        c = resp.choices[0]
        u = resp.usage
        print(f"\n{'='*60}")
        print(f"[{name}]")
        print(f"  content:       {'「' + c.message.content + '」' if c.message.content else '(空)'}")
        print(f"  tool_calls:    {len(c.message.tool_calls) if c.message.tool_calls else 0}")
        print(f"  finish_reason: {c.finish_reason}")
        print(f"  prompt_tokens: {u.prompt_tokens}")
        print(f"  compl_tokens:  {u.completion_tokens}  ← 非零说明模型生成了内容")
        print(f"  total_tokens:  {u.total_tokens}")
    except Exception as e:
        print(f"\n[{name}] ERROR: {e}")


async def main():
    print("智谱 GLM-5 Coding Plan 端点测试")
    print(f"Endpoint: {API_BASE}")

    # Test 1: 纯对话，不带 tools
    await call("Test 1: 纯对话 (无 tools)")

    # Test 2: 纯对话 + response_format
    await call("Test 2: 纯对话 + json_object", response_format={"type": "json_object"})

    # Test 3: 带 dummy tool
    await call("Test 3: 带 dummy tool", tools=DUMMY_TOOL)

    # Test 4: 带 dummy tool + response_format
    await call("Test 4: dummy tool + json_object", tools=DUMMY_TOOL, response_format={"type": "json_object"})

    # Test 5: 带真实天气查询工具
    await call("Test 5: 带 get_weather 工具", tools=WEATHER_TOOL)

    # Test 6: 天气工具 + json_object
    await call("Test 6: get_weather + json_object", tools=WEATHER_TOOL, response_format={"type": "json_object"})

    print(f"\n{'='*60}")
    print("结论：Test 1/2 content 为空但 compl_tokens > 0 = 生成了但没返回")
    print("      Test 3-6 加了 tool 就有内容返回")
    print("      Test 5/6 模型应该会调用 get_weather 工具")


if __name__ == "__main__":
    asyncio.run(main())
