# Personal Agent

面向招聘者和技术面试官的聊天式 AI 数字分身。它用第一人称介绍经过授权的项目资料，同时明确披露为 AI，不代表本人实时在线。应用构建在固定版本的 `vendor/wengraph` 子模块之上。

## Highlights

- 基于自研 WenGraph 的受控 ReAct 图：只读检索工具经 CapabilityPolicy、RiskPolicy 与 ToolGuard 约束。
- Markdown 私有资料的本地混合检索：Sentence Transformers 语义检索和 SQLite FTS5 关键词检索。
- 面向访客的安全引用合同：原始 Markdown、文件路径和私有内容不会进入 HTTP 响应。
- 无登录的标签页会话：前端 `sessionStorage` 隔离会话，后端 SQLite 临时记忆 24 小时过期。
- 可控服务容量：持久化 Run、固定 async Worker、有界队列、HTTP 轮询和单进程部署约束。

## Architecture

React 单页界面通过普通 HTTP 提交问题。FastAPI 创建持久化 Run 并返回 `202`，浏览器轮询 Run 状态。单个进程内的有界 `asyncio.Queue` 由固定数量 async Worker 消费；SQLite 保存 Run、临时会话和知识索引。Agent 每轮先做混合检索，再在 WenGraph 的只读 ToolGuard 边界内按需进行二次检索。

每个浏览器标签页在 `sessionStorage` 中保存独立的随机会话 ID。服务端临时对话与回答保留 24 小时；无登录、无跨设备同步、无会话列表。部署必须保持一个 Uvicorn worker，因为队列是单进程内存结构。

## Local Development

需要 Python 3.12 与 Node.js 22+。初始化子模块后安装依赖：

```bash
git submodule update --init --recursive
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
cd frontend && npm install && cd ..
cp .env.example .env
```

在 `.env` 填写服务器端 OpenAI-compatible 配置。真实密钥不会进入前端、Git 或 Docker 镜像。
[`model.yaml`](model.yaml) 是可提交的模型连接配置示例；实际运行以环境变量 `OPENAI_BASE_URL`、`OPENAI_API_KEY` 和 `OPENAI_MODEL` 为准。

### Quick Preview Without Personal Documents

即使尚未准备知识资料，也可以先验证页面、队列、轮询和通用技术问答：

```bash
set -a && source .env && set +a
.venv/bin/uvicorn personal_agent.api.bootstrap:create_production_app --factory --port 8000
cd frontend && npm run dev
```

打开 `http://127.0.0.1:5173`。没有资料时，通用技术问题可以回答；涉及个人项目、经历或贡献的问题会明确说明资料未覆盖，不会编造。

准备 Markdown 知识资料并建索引：

```bash
.venv/bin/python -m personal_agent.knowledge.cli knowledge --database data/knowledge.db
```

运行 API 与前端开发服务器：

```bash
.venv/bin/uvicorn personal_agent.api.bootstrap:create_production_app --factory --reload --port 8000
cd frontend && npm run dev
```

开发页面地址是 `http://127.0.0.1:5173`，Vite 会代理 `/api` 到 API。生产模式由 FastAPI 直接提供构建后的前端文件。

## Knowledge Documents

知识材料采用 Markdown 加 YAML front matter。原始正文可以是私有材料，但页面只收到审核过的 `public_summary` 与可选 `public_url`，绝不暴露本地路径或原始 Markdown。

```markdown
---
source_id: wengraph-overview
project: WenGraph
title: WenGraph 架构说明
visibility: private
public_summary: 自研 Agent 图运行时，关注可控执行与工具治理。
public_url: https://github.com/bq-wen/wengraph
---
# WenGraph

这里是供 Agent 检索的授权材料正文。
```

`source_id` 必须稳定且只包含小写字母、数字、`-` 或 `_`。生产索引使用本地 Sentence Transformers；`--hash-embedding` 仅供无模型下载的测试，不适用于生产检索。

## Docker Deployment

```bash
cp .env.example .env
# 填写 OPENAI_API_KEY、OPENAI_BASE_URL、OPENAI_MODEL
docker compose build
docker compose run --rm personal-agent python -m personal_agent.knowledge.cli /app/knowledge --database /app/data/knowledge.db
docker compose up -d
```

访问 `http://localhost:8000`。首次索引会下载配置的 embedding 模型到 Docker volume；随后可离线重建索引。`./data` 保存 SQLite 数据库，`./knowledge` 保存可版本控制的资料源。若未来需要多实例部署，请将队列替换为 Redis/Celery/Dramatiq 等共享调度器，不能简单增加 Uvicorn worker 数。

## Security Notes

- `.env` 被 Git 忽略，绝不提交模型 Key；若 Key 曾出现在聊天记录或日志中，请立即在模型服务商控制台轮换。
- 公开引用只使用知识文档 front matter 中审核过的 `public_summary` 与 `public_url`。
- 当前队列仅支持一个 Uvicorn worker。横向扩展前必须引入共享调度层。

## Verification

```bash
python3.12 -m pytest -q
cd frontend && npm run lint && npm run build
```
