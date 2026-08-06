# HANDOFF — Personal Agent 交接文档

> **写给下一台机器上的 Agent**：这是「个人数字分身（distill-agent 模板）」项目的完整交接。
> 本文档覆盖：项目是什么、当前状态、最近的 review 与修复、架构关键知识、运行验证方法、
> 已知限制与下一步待办。读完后你应该能直接继续开发。
>
> 交接日期：2026-08-06 ｜ 交接人：bq-wen（本仓库 git 身份）

---

## 0. 快速上手（30 秒）

- **项目**：通用数字分身模板。给一份原始资料 → 后台蒸馏流水线（SUPERVISED + 人工审批）加工成知识库 → 在线分身（UNATTENDED 只读检索）回答访客问题。
- **核心叙事**：自研图运行时 WenGraph（`vendor/wengraph` 固定 submodule），同一套工具治理框架（CapabilityPolicy + RiskPolicy + ToolGuard）管两种风险场景。
- **技术栈**：Python 3.12 + FastAPI + asyncio 有界队列 + SQLite（embedding + FTS5）+ Sentence Transformers（bge-small-zh-v1.5）+ React 18 + TypeScript + Vite + Docker。
- **仓库**：`git@github.com:bq-wen/personal-agent.git`，分支 `main`。**WenGraph 是 submodule**：clone 后必须 `git submodule update --init --recursive`。

---

## 1. 仓库布局

```
personal-agent/
├── README.md                 # 项目入口文档（含安全边界、部署、验证）
├── AGENTS.md                 # 本仓库的开发约束（务必遵守）
├── HANDOFF.md                # ← 本文档
├── model.yaml                # 可提交的模型连接示例（密钥只放 .env）
├── .env.example              # 环境变量模板（.env 被 gitignore，绝不提交）
├── compose.yaml / Dockerfile # 单进程部署（一个 Uvicorn worker，勿改多 worker）
├── knowledge/profile.md      # 身份来源文档（profile: true，驱动 persona/前端）
├── data/                     # 运行时 SQLite（gitignore）
├── personal_agent/
│   ├── api/                  # HTTP 边界：app.py（路由/校验/限流）、bootstrap.py（生产组装）、rate_limit.py
│   ├── application/          # 在线分身：service.py（单轮回答）、graph.py（ReAct 图装配）、runs.py（队列/worker/持久化/定时清理）、tools.py（只读检索工具）、conversations.py、profile.py、contracts.py
│   ├── knowledge/            # 检索层：store.py（SQLite+FTS5）、retrieval.py（混合检索）、chunking.py、embedding.py、documents.py、models.py、cli.py
│   ├── distillation/         # 蒸馏链路：nodes.py（流水线节点）、graph.py（SUPERVISED 图）、runner.py（run/approve 流程）、tools.py（写审计/写库工具）、contracts.py、cli.py
│   ├── contracts.py          # 共享公开契约（SourceMetadata/PublicCitation 等）
│   ├── settings.py           # 环境配置（容量/限流/阈值）
│   └── wengraph_runtime.py   # ⚠️ 唯一的 WenGraph 导入边界（应用层只能从这里 import）
├── vendor/wengraph/          # 固定 submodule，不要直接改框架源码
├── frontend/                 # React SPA（main.tsx 单文件 + styles.css）
├── doc/                      # 学习/面试文档（architecture/serving-flow/distillation/rag/governance/tech-stack）
└── tests/                    # pytest（当前 45 个，全过）
```

**分层铁律**（AGENTS.md）：HTTP 关注点只在 `api/`；Agent 与知识行为在 `application/` 与 `knowledge/`；跨层用 Pydantic 契约；API 响应绝不暴露原始 Markdown 路径、私有正文、SQLite 行；WenGraph 只从 `wengraph_runtime.py` 导入。

---

## 2. 当前状态（已实现，全部通过验证）

### 在线展示链路（UNATTENDED）
- `POST /api/conversations/{id}/messages` → 202 + run_id；`GET /api/runs/{run_id}` 轮询。
- 有界 asyncio 队列（`QUEUE_SIZE=20`）+ 固定 worker（`QUEUE_WORKERS=2`），队列满 429，同会话并发 409。
- 强制首轮混合检索（语义阈值 `MINIMUM_SEMANTIC_SCORE=0.35` + FTS5 关键词），ReAct 图内只读工具可二次检索。
- persona 由 `knowledge/profile.md` 动态生成；`/api/profile`、`/api/topics` 数据驱动前端。
- 会话：前端 `sessionStorage` 随机会话 ID；服务端 24h TTL（进程内定时清理，每 30 分钟）。
- 引用合同：回答附带 citations，只含 front matter 审核过的 `public_summary`/`public_url`。

### 蒸馏链路（SUPERVISED）
- 流水线：SourceLoader → Cleaner → Extractor(LLM) → Structurer → ContentRouter → AuditGate → Indexer。
- `write_audit_artifact`（MEDIUM）与 `index_documents`（HIGH）在 SUPERVISED 下强制 `REQUIRE_APPROVAL`；checkpoint 持久化到 SQLite，支持跨进程 `approve` 恢复。
- CLI：`run --input ... --database ... [--yes|--incremental]`、`approve --run <id> [--reject]`。
- 增量模式：SHA-256 哈希记录在 `data/distill/state.json`，只处理变更文件。
- 审计产物：`data/distill/audit/<run_id>.json`（原子 + 文档，可溯源）。

### 测试
- 45 个 pytest 全部通过（用脚本化 ChatModel/EmbeddingProvider，零外部依赖、可离线复跑）。
- 前端 `npm run lint`（`--max-warnings=0`）与 `npm run build` 零告警。

---

## 3. 最近一次会话：全面 review + 修复（2026-08-06）

### 3.1 本次图结构 review 与修复

项目实际是外围 HTTP/队列编排、在线六节点 ReAct 图、蒸馏十六节点审批图。本次已修复：蒸馏拒绝/失败不再写增量 state；detached approve 每次只批准一个 checkpoint；增量运行通过受 ToolGuard 保护的写库工具清理删除源；state.json 兼容旧版 hash 并迁移到 content_hash/source_id；公开元数据不暴露原始路径；在线图 max_steps 从 12 调整到 24；删除未引用的 _existing_refs()。45 个后端测试通过。

### 3.2 真实 DeepSeek 体验

已使用 OpenAI-compatible 配置完成真实请求：

OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
OPENAI_API_KEY 只通过进程环境注入，绝不提交。

适配器会自动请求 base_url 加 /chat/completions，因此不要给 base_url 追加 /v1。已新增 knowledge/placeholder-project.md，并用 BAAI/bge-small-zh-v1.5 重建知识库；当前有 profile 和 placeholder-project 两个来源，共 2 个 chunk。

模型已缓存时可离线加载：设置 HF_HOME、HF_HUB_OFFLINE=1、TRANSFORMERS_OFFLINE=1。另一台主机若没有模型缓存，先不要打开离线变量，让 Hugging Face 下载完成。

### 3.3 TIMEOUT=5S 诊断

当前知识库没有源码文件或 TIMEOUT=5S 内容。对 TIMEOUT=5S 的关键词召回为空，语义召回只返回 profile/占位资料的低相关结果，因此模型不知道该源码设置，根因是知识覆盖/索引不足，不是 ToolGuard 拒绝。

持久化 Run 中有一次旧请求失败，原因是工具调用次数达到上限 4；另一次是客户端已关闭。没有证据显示 search_personal_semantic 或 search_personal_keywords 被 DENY/REQUIRE_APPROVAL 拒绝；在线工具是 READ_ONLY、LOW、UNATTENDED，正常会放行。

如果要回答 TIMEOUT=5S，必须把允许公开的源码片段整理为带 YAML front matter 的 Markdown 后重新索引。在线 Agent 不会自动读取 Git 工作树源码。

对全项目做了逐文件 review（后端 2874 行 + 前端 + 6 篇文档 + WenGraph 治理核心），并完成图结构 review 与工作流修复，测试当前为 45 个。**
`fix: harden run_id validation, rate limiting, cleanup, and docs` 之类的提交**，diff 范围：

| 文件 | 改动 |
|---|---|
| `distillation/contracts.py` | 新增 `RUN_ID_PATTERN`；`AuditArtifact.run_id` 加 pattern |
| `distillation/tools.py` | `WriteAuditArguments.run_id` 加 pattern；execute 内最后防线校验 |
| `distillation/runner.py` | `run_pipeline`/`approve_run` 入口 `_validate_run_id` |
| `api/app.py` | conversation_id/run_id 路径参数 pattern（非法→422）；限流接入；`Path` 别名避免与 pathlib 冲突 |
| `api/rate_limit.py` | **新增**：固定窗口内存限流器 |
| `api/bootstrap.py` | 生产 app 启用限流（`resolved_settings` 归一化修复） |
| `settings.py` | 新增 `rate_limit_per_minute`（`PERSONAL_AGENT_RATE_LIMIT_PER_MINUTE=30`） |
| `application/runs.py` | scheduler 定时清理任务（默认 30 分钟）+ `_run_cleanup()` 抽取 |
| `application/service.py` | 混合检索合并顺序：语义命中优先于 FTS |
| `knowledge/store.py` | `import re` 上移 |
| `README.md` | 安全边界表述修正（硬边界=引用卡片；软边界=回答正文） |
| `doc/*`（7 篇） | CLI 命令对齐、PII 脱敏诚实标注、FTS 中文限制、过期"目标态"删除、重复段清理 |
| `tests/*`（3 个文件） | 新增 5 个测试：run_id 三层拦截、非法 ID 422、限流 429、cleanup 过期清理 |

**修复要点（继续开发时必须维护）**：
- `run_id` 白名单 `^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$` —— 它是 audit 文件名与 SQLite 主键，**任何拼接 run_id 进路径的代码都要过这道校验**。
- 限流是**单进程内存实现**，扩展多 worker 时必须换共享限流器（与队列同命运）。
- 回答正文（`answer.text`）是提示词级软约束，可能复述私有资料；引用卡片是硬边界。**不要删除 README 里关于这条边界的说明，更不要写成"私有内容绝不进响应"。**

---

## 4. 架构关键知识（继续开发前必读）

### 4.1 治理模型（项目灵魂）
```
节点发出 ToolRequest → ToolGuardNode（CapabilityPolicy 管"谁能用" + RiskPolicy 管"模式×风险→决策"）
  → PolicyRouterNode 路由：
     ALLOW            → ToolNode 执行
     REQUIRE_APPROVAL → ApprovalEndNode 暂停（checkpoint 持久化，resume 恢复）
     DENY             → DenyEndNode 反馈拒绝原因
```
- 展示链路：UNATTENDED + READ_ONLY(LOW) → 全自动放行。
- 蒸馏链路：SUPERVISED + IDEMPOTENT_WRITE(MEDIUM/HIGH) → 强制人工审批。
- executor 对节点做 State 字段读写白名单（ReadPolicy/WritePolicy），越权抛 `PermissionDeniedError`。
- 重复工具调用检测：按 tool_name+arguments 签名，防 LLM 换 call_id 绕圈。

### 4.2 数据流
```
蒸馏：raw/ → Cleaner → LLM 提炼 KnowledgeAtom[] → Structurer 生成 front matter 文档
  → 人工审批 → PersonalKnowledgeService.index_document（chunk + embedding + FTS5 入 SQLite）
在线：问题 → 首轮混合检索（evidence 注入 message）→ ReAct 图（LLM↔只读工具）
  → 回答 + PublicCitation[] → 202/轮询
```

### 4.3 必须牢记的约束
- **单 Uvicorn worker**（队列进程内）。多实例必须先换 Redis/Celery 共享调度。
- **密钥只在 .env**（gitignore）。`model.yaml` 是示例，可提交。
- 知识文档必须带 YAML front matter（`source_id` 小写字母数字 `-_`、`visibility`、`public_summary`）。
- 检索阈值/容量参数全部走 `settings.py`（环境变量）。
- Docker 内非 root 运行（`appuser`），`HF_HOME=/app/models` 持久化 embedding 模型。

---

## 5. 运行与验证（另一台机器的标准流程）

```bash
# 准备
git submodule update --init --recursive          # 拉 WenGraph
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cd frontend && npm install && cd ..
cp .env.example .env                              # 填 OPENAI_API_KEY/BASE_URL/MODEL

# 建索引（profile.md 已就绪）
.venv/bin/python -m personal_agent.knowledge.cli knowledge --database data/knowledge.db

# 起服务（dev：Vite 代理 /api 到 8000）
.venv/bin/uvicorn personal_agent.api.bootstrap:create_production_app --factory --port 8000
cd frontend && npm run dev

# 蒸馏（交互式审批 / 自动批准）
.venv/bin/python -m personal_agent.distillation.cli run --input <raw_dir> --database data/knowledge.db
.venv/bin/python -m personal_agent.distillation.cli approve --run distill-xxx --database data/knowledge.db

# 验证
.venv/bin/python -m pytest -q                    # 45 passed
cd frontend && npm run lint && npm run build
```

---

## 6. 已知限制与下一步待办（按优先级）

### 已识别但未实现（建议下一台机器优先做）
1. **程序级 PII 脱敏（P1）**：蒸馏 `CleanerNode` 目前只做格式去噪，手机号/邮箱/他人姓名依赖 prompt 规则 + 人工审批。建议在 Cleaner 加正则脱敏（`\d{11}` 手机号、邮箱模式、身份证），并在 `doc/distillation-pipeline.md` 更新"脱敏边界"段落。
2. **检索评估集（P1，面试加分）**：建 20-30 条「问题 → 期望 source_id」QA 集，跑 recall@k。文档已列为目标态。
3. **FTS 中文分词增强（P2）**：`unicode61` 对连续汉字整体切分，"工具安全" 查不到 "工具调用安全"。可换 jieba 分词或 n-gram（SQLite FTS5 tokenizer 需自定义或预处理）。
4. **回答正文出站审核（P2，安全）**：若要硬保证私有正文不进 HTTP，需在出站前对 `answer.text` 做脱敏/摘要审核（当前只有 persona 软约束）。

### 文档里已写的增强点（低优先）
- SSE 流式替代 700ms 轮询；Redis 队列多 worker；GitHub Actions CI（pytest+lint+build）；Nginx+HTTPS；Alembic 迁移；前端 openapi-typescript 类型生成。

### 实现中的小观察（可留意，非缺陷）
- `service.py` 会话事件 created_at 用微秒间隔保证顺序，理论上有同一微秒的极小竞态；若换存储需保证有序。
- 前端 `pollRun` 无限轮询直到终态（服务端 90s 超时 + 重启标记 interrupted 有兜底，可接受）。
- `doc/tech-stack-interview.md` 的 CLI/参数描述已与实现同步，改 CLI 时记得同步。

---

## 7. Git / 安全纪律

- push 身份：仓库 local config 已是 `bq-wen <1585622347@qq.com>`；remote 为 SSH `git@github.com:bq-wen/personal-agent.git`。
- **绝不提交**：`.env`（真实 key）、`data/`（SQLite 含对话/回答）、`frontend_dist/`、`node_modules/`（均已 gitignore，push 前 `git status` 复核）。
- commit 风格：conventional commits，中文描述（`feat:`/`fix:`/`docs:`/`test:`）。
- **不要修改 `vendor/wengraph` 的源码**；需要改框架时先讨论，或另开仓库 PR。
- 改 `.env.example` 增加配置项时，同步更新 README 与 `settings.py` 文档串。
