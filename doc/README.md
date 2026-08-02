# doc/ —— 学习与复习文档

本项目重构为**通用蒸馏 Agent 模板（distill-agent）**：一条图运行时（WenGraph），两张子图——
**展示链路**（在线 AI 分身）与**蒸馏链路**（后台知识处理流水线）。

这些文档用于你在面试前理解项目、复习概念。每篇包含：**背景概念 → 项目实现 → 面试怎么讲**。
展示链路（profile/topics 数据驱动）与蒸馏链路（SUPERVISED 审批流水线）均已实现；标「目标态」的章节为规划中的增强点。

## 文档索引

| 文档 | 内容 | 优先级 |
|---|---|---|
| [architecture.md](architecture.md) | 整体架构：两条链路、一个运行时、数据契约 | ★★★ 先读 |
| [serving-flow.md](serving-flow.md) | 展示链路：ReAct 图、检索工具、异步队列、前端 | ★★★ |
| [distillation-pipeline.md](distillation-pipeline.md) | 蒸馏链路：清洗→提炼→审批→索引 全流程 | ★★★ |
| [rag-retrieval.md](rag-retrieval.md) | RAG 与混合检索：embedding、chunking、FTS5、阈值 | ★★★ |
| [governance-security.md](governance-security.md) | 治理模型：ToolGuard / 风险策略 / 引用合同 | ★★ |
| [tech-stack-interview.md](tech-stack-interview.md) | 技术栈面试要点：深挖区 / 广度和加分区 / 高频问题 | ★★★ 面试前必读 |

## 建议阅读顺序

1. `architecture.md` → 建立全局图景（30 分钟）
2. `serving-flow.md` + `governance-security.md` → 理解在线服务如何工作（1 小时）
3. `rag-retrieval.md` → 理解检索原理（这是 RAG 面试核心，1 小时）
4. `distillation-pipeline.md` → 理解蒸馏闭环（30 分钟）
5. `tech-stack-interview.md` → 面试前冲刺（反复看）

## 快速速记（30 秒版）

- **项目是什么**：给一份资料，变成那个人的 AI 分身；资料由后台蒸馏流水线自动处理。
- **技术主线**：Python 3.12 + FastAPI + asyncio 队列 + SQLite（embedding+FTS5 混合检索）+ 自研 WenGraph 图运行时（受控 ReAct）+ React 18 + TypeScript + Docker。
- **三个卖点**：① 自研受控 Agent 图（不是裸调 LLM）；② 一套治理框架管两种风险模型（只读服务 / 带人工审批的蒸馏写库）；③ 知识可溯源、可审批（AI 数据治理）。
- **约束**：单 Uvicorn worker（队列是进程内结构）；密钥只在 .env；公开响应只含 `public_summary`/`public_url`。
