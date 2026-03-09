# 飞书 Bot 创建指南

## 1. 创建飞书应用

1. 访问 [飞书开放平台](https://open.feishu.cn/) → 开发者后台
2. 点击「创建企业自建应用」
3. 填写应用名称（如 "OrderGuard 数据助手"）和描述
4. 创建完成后，记下 **App ID** 和 **App Secret**

## 2. 添加机器人能力

1. 在应用页面 → 添加应用能力 → 选择「机器人」
2. 机器人名称可自定义（如 "数据助手"）

## 3. 配置事件订阅

1. 进入「事件订阅」页面
2. 请求地址填写: `https://your-domain/api/feishu/event`
   - 开发时可用 ngrok 等工具暴露本地端口
3. 订阅事件:
   - `im.message.receive_v1`（接收消息）
4. 记下 **Verification Token** 和 **Encrypt Key**（如果启用加密）

## 4. 配置权限

在「权限管理」中申请以下权限:

| 权限 | 说明 |
|------|------|
| `im:message` | 获取与发送单聊、群组消息 |
| `im:message:send_as_bot` | 以应用的身份发送消息 |

## 5. 发布应用

1. 在「版本管理与发布」中创建版本
2. 提交审核（企业内部应用通常自动通过）
3. 发布后，将机器人添加到群聊中

## 6. 配置 OrderGuard

### 环境变量 (.env)

```bash
FEISHU_APP_ID=cli_xxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxx
```

### 配置文件 (config.yaml)

```yaml
feishu_bot:
  enabled: true
  app_id: "${FEISHU_APP_ID}"
  app_secret: "${FEISHU_APP_SECRET}"
  verification_token: "${FEISHU_VERIFICATION_TOKEN}"
  max_turns: 10
  context_ttl_minutes: 30
  permissions:
    - user_ids: ["*"]                    # 所有人可查测试库
      mcp_servers: ["test-warehouse"]
    - user_ids: ["u_admin1", "u_admin2"] # 管理员可查生产库
      mcp_servers: ["production-erp", "analytics-db"]
```

## 7. 启动服务

```bash
uv run order-guard serve
```

## 8. 使用方式

在飞书群中 @机器人 发送消息:

- `@数据助手 查一下仓库库存`
- `@数据助手 最近7天退货率最高的SKU是哪些？`
- `@数据助手 SKU-001 的销售趋势怎么样？`

## 常见问题

### 本地开发如何测试？

使用 ngrok 暴露本地端口:

```bash
ngrok http 8000
```

将 ngrok 地址填入飞书事件订阅的请求地址。

### 权限如何管理？

- `user_ids: ["*"]` 表示所有用户
- 指定用户 ID 可在飞书管理后台查看
- 不同用户组可配置不同的数据源访问权限
