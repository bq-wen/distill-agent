# 展示链路（服务子图）详解

在线 AI 分身的完整请求路径：**前端 → FastAPI → 队列 → 图执行 → 检索 → 回答 + 引用**。

## 1. 一次提问的旅程

```
访客输入问题
  │  POST /api/conversations/{id}/messages
  ▼
FastAPI: 校验 Pydantic 契约 → 检查会话冲突(409) → 提交队列(满则 429)
  │ 202 Accepted { run_id }
  ▼
asyncio.Queue（有界，容量 PERSONAL_AGENT_QUEUE_SIZE=20）
  ▼ 固定 worker（PERSONAL_AGENT_QUEUE_WORKERS=2）消费
GraphExecutor 执行 ReAct 图：
  LLMNode: 把会话历史 + 检索工具描述 + 问题喂给模型
    → 模型决定：直接回答(final) 还是 调用检索工具(tool_call)
  AgentRouterNode 按条件路由
    → tool_call: ToolGuardNode 检查能力+风险（UNATTENDED+只读=LOW → ALLOW）
    → PolicyRouterNode → ToolNode 执行 search_personal_semantic / search_personal_keywords
    → 检索结果回到 LLMNode（第二轮推理，依据资料组织回答）
    → final: AgentFinishNode，收集 citations
  │
  ▼ 写 SQLite run 表（status: queued/running/completed/failed/expired）
前端 700ms 轮询 /api/runs/{run_id} → completed 后渲染回答 + 引用卡片
```

## 2. 关键组件（对应代码）

| 组件 | 代码位置 | 说明 |
|---|---|---|
| 图装配 | `personal_agent/application/graph.py` | `build_personal_graph()`，固定 submodule 的 import 边界在 `wengraph_runtime.py` |
| 工具 | `personal_agent/application/tools.py` | 两个只读检索工具，`ToolEffect.READ_ONLY` |
| 队列/worker | `personal_agent/application/runs.py` | `PersonalRunScheduler`，有界队列 + 固定 worker + run 持久化 |
| HTTP | `personal_agent/api/app.py` | 202/409/429/503 语义，静态前端托管 |
| 前端 | `frontend/src/main.tsx` | React 单文件 SPA：会话、轮询、引用卡片 |

## 3. 两个只读检索工具

```python
search_personal_semantic(query, limit=5)   # 语义检索：向量余弦 → 阈值过滤
search_personal_keywords(query, limit=5)   # 关键词：SQLite FTS5 精确匹配
```

设计要点：
- 都是 `READ_ONLY` 副作用、LOW 风险 → UNATTENDED 模式直接放行
- 工具返回的检索内容**带来源标记**（`[来源：标题|source_id]` + 章节 + 正文片段），LLM 被要求回答末尾标明使用过的来源标题
- 引用对象是**审核过的公开元数据**（`public_summary`/`public_url`），原始 Markdown 和本地路径绝不出 HTTP 响应（见 governance-security.md）

## 4. Persona（人格约束）如何工作

现状：persona 由 `knowledge/profile.md`（front matter 身份字段 + 正文画像）动态生成——
`personal_agent/application/profile.py` 的 `build_persona_prompt(load_profile(store))`，
无 profile 文档时回落中性默认人格。核心约束：
- 明确披露是 AI 数字分身，不是本人实时在线
- 个人经历/事实必须来自检索结果，**资料不足时明确说"资料没覆盖，建议问本人"，绝不编造**
- 通用技术问题可以答，但不等同于个人经历；私密信息说明不在公开范围

> 说明：persona 是提示词级软约束——模型组织回答时能看到检索到的私有正文，理论上可能复述其中内容。引用卡片（元数据）是硬边界，正文边界依赖 persona 提示与部署隔离（内网/限流）；如需硬保证，应在出站前做脱敏或摘要审核。

## 5. 身份与推荐问题（数据驱动）

- `GET /api/profile`：身份响应（name/monogram/role/github/greeting/covered_topics），来自 `knowledge/profile.md`；未载入返回 404，前端回落默认身份
- `GET /api/topics`：按 project 分组的推荐问题（front matter `public_questions` + 索引元数据），前端据此渲染推荐问题按钮
- 换一份 profile + 资料目录重新索引 → 姓名、monogram、简介、推荐问题全部跟随变化，无需改前端代码

## 6. 会话与记忆

- 每个浏览器标签页 `sessionStorage` 一个随机会话 ID → 无登录、无跨设备同步、无会话列表
- 服务端临时会话 + run 保留 24 小时（`PERSONAL_AGENT_CONVERSATION_TTL_HOURS`）
- 多轮对话上下文由 `ContextBuilder` 组装进 LLM 请求

## 7. 面试高频追问

**Q: 为什么队列满返回 429？**
A: 背压（backpressure）。LLM 调用是慢外部依赖，无界队列会让内存和延迟失控；固定 worker + 有界队列让系统"拒绝得优雅"而不是"拖垮"。

**Q: 为什么只能一个 Uvicorn worker？**
A: 队列是进程内 `asyncio.Queue`。多 worker = 多队列 = 请求被不同进程处理、run 状态跨进程不可见。扩展路径：把队列换成 Redis/Celery 共享调度（README 已写明）。

**Q: 前端轮询 vs 流式？**
A: 轮询实现简单、状态明确，是演示的稳妥选择；体验升级方向是 SSE（服务端推送逐 token），作为后续增强点。

**Q: 会话冲突 409 是什么？**
A: 同一会话同时提交多条消息会冲突（一个会话同一时刻只跑一个 run），用 409 拒绝并发，保证上下文一致性。

## 8. 代码阅读路线

```
frontend/src/main.tsx          → 前端全貌（180 行，读 10 分钟）
personal_agent/api/app.py      → HTTP 边界与状态码语义
personal_agent/application/runs.py → 队列/worker/持久化
personal_agent/application/graph.py → 图装配与 persona
personal_agent/application/tools.py → 工具与引用收集
```
