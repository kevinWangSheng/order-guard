# T01: 项目骨架 — 验收标准

## Given/When/Then

- Given 项目代码已拉取, When 执行 `uv sync`, Then 所有依赖安装成功无报错
- Given 依赖已安装, When 执行 `uv run python -m order_guard.main`, Then FastAPI 应用启动并监听端口
- Given FastAPI 已启动, When 请求 GET /health, Then 返回 `{"status": "ok"}`
- Given 依赖已安装, When 执行 `uv run order-guard --version`, Then 输出版本号
- Given Docker 已安装, When 执行 `docker compose build`, Then 镜像构建成功
- Given 镜像已构建, When 执行 `docker compose up`, Then 服务启动并可访问 /health

## Checklist

- [ ] pyproject.toml 包含所有核心依赖
- [ ] src/order_guard/ 下所有子目录都有 __init__.py
- [ ] FastAPI 应用可正常启动
- [ ] CLI 命令可正常执行
- [ ] loguru 日志输出到控制台和文件
- [ ] Docker 构建和运行无报错
- [ ] .env.example 包含所有需要的环境变量占位
