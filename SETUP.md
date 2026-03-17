# OrderGuard 开发环境准备

## 开发必备

### 本地环境
- Python 3.11+
- uv（Python 包管理）：`curl -LsSf https://astral.sh/uv/install.sh | sh`
- Docker + Docker Compose（可选，用于容器化部署测试）

### API Key（T06 开始需要，T01-T05 不需要）

选一个即可，推荐开发阶段用国内平台（免费额度大、无需 VPN）：

| 平台 | 注册 | 免费额度 | 环境变量 |
|------|------|---------|---------|
| **阿里百炼 (Qwen)** | https://bailian.console.aliyun.com/ | 70M+ tokens | `OG_LLM_API_KEY=sk-xxx` + `OG_LLM_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1` |
| **DeepSeek** | https://platform.deepseek.com/ | 新用户赠送 | `OG_LLM_API_KEY=sk-xxx`（LiteLLM 自动识别 deepseek/ 前缀） |
| **OpenRouter** | https://openrouter.ai/ | 免费模型无限用 | `OG_LLM_API_KEY=sk-xxx` + `OG_LLM_API_BASE=https://openrouter.ai/api/v1` |
| **OpenAI** | https://platform.openai.com/ | 新用户 $5 | `OG_LLM_API_KEY=sk-xxx` |

config.yaml 中对应配置：
```yaml
# 阿里百炼
llm:
  model: "openai/qwen-plus"
  api_base: "https://dashscope.aliyuncs.com/compatible-mode/v1"

# DeepSeek
llm:
  model: "deepseek/deepseek-chat"

# OpenRouter（免费模型）
llm:
  model: "openrouter/deepseek/deepseek-chat-v3-0324:free"
  api_base: "https://openrouter.ai/api/v1"

# OpenAI
llm:
  model: "gpt-4o"
```

## 后续版本才需要（现在不用准备）

| 项目 | 说明 | 什么时候需要 |
|------|------|------------|
| NetSuite API 凭证 | OAuth 2.0 / Token-Based Auth | 接真实 ERP 数据源时 |
| 飞书 App ID + Secret | 飞书开放平台创建应用 | Bot 对话接入时 |
| 企业微信 CorpID + Secret | 企业微信管理后台创建应用 | Bot 对话接入时 |
| 飞书/企业微信 Webhook URL | 群机器人 Webhook 地址 | 测试真实推送时（MVP 可用任意 URL 测试） |
| 领星 API Key | 领星 ERP 开发者后台 | 接领星数据时 |
| Google Drive API | Google Cloud Console 创建凭证 | 接 Google Drive 文档时 |

## 快速开始（项目搭建后）

```bash
# 1. 克隆项目
cd order-guard

# 2. 安装依赖
uv sync

# 3. 复制配置
cp config.example.yaml config.yaml
cp .env.example .env

# 4. 编辑 .env 填入 API Key
# OG_LLM_API_KEY=your-key-here

# 5. 启动服务
uv run order-guard serve

# 6. 或手动执行一次检测
uv run order-guard run --dry-run
```
