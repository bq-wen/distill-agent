# RAG 与混合检索原理（项目实现详解）

本项目是典型的 **RAG（Retrieval-Augmented Generation）**：先检索相关知识，再让 LLM 基于检索结果回答。这篇讲透项目里的每一步，RAG 是面试提问重灾区。

## 1. RAG 全链路（本项目的每一步）

```
原始文档(前 matter Markdown)
  → chunking（按标题分块）
  → 每块算向量（本地 embedding 模型 bge-small-zh-v1.5）
  → 存 SQLite（向量 + 原文 + FTS5 关键词索引）
查询时：
  → 向量检索（余弦相似度）取 top-k
  → 关键词检索（FTS5）取 top-k
  → 合并去重 → 低于阈值的丢弃（MINIMUM_SEMANTIC_SCORE=0.35）
  → 拼进 prompt → LLM 生成回答（引用来源）
```

对应代码：`personal_agent/knowledge/`（documents / chunking / embedding / retrieval / store）+ `application/tools.py`。

## 2. 向量检索（语义）

**概念**：把文本映射到高维向量空间，语义相近的文本向量夹角小。用余弦相似度衡量。

**本项目的选择：**
- 模型：`BAAI/bge-small-zh-v1.5`——中文场景常用的轻量模型（约 100MB），单机 CPU 可跑
- 推理设备：`--device cpu`（演示环境无 GPU）
- 实现：不是用向量数据库，而是 SQLite 存 `embedding` 列 + 全量余弦 + 阈值过滤（chunk 量小，毫秒级）
- 阈值：`PERSONAL_AGENT_MINIMUM_SEMANTIC_SCORE=0.35`——低于此分数视为"没检索到相关资料"，避免拿不相关的片段硬答

**面试要点**：能说出"为什么 bge-small-zh"（中文效果好/体积小/单机可跑）、"为什么余弦"（归一化后等价内积，简单快）、"为什么 0.35"（拍出来的初始值，需评估集校准——后续可加检索评估增强点）。

## 3. 关键词检索（FTS5）

**概念**：SQLite 内置全文检索 FTS5，倒排索引做子串/分词匹配，精确命中专有名词（项目名、接口名）。

**为什么需要混合检索？**
- 纯向量：专有名词（如 "WenGraph"、"ToolGuard"）在向量空间容易被"稀释"——语义相近但名词不同
- 纯关键词：同义表达（"你们这个框架怎么保证安全" vs "ToolGuard 怎么治理工具"）匹配不到
- 混合 = 两条路都走，互补

对应工具：`search_personal_keywords`（FTS5）/ `search_personal_semantic`（向量）。

## 4. Chunking（分块）

`knowledge/chunking.py`：按 Markdown 标题分块，每块带 heading 上下文，保证：
- 块内有语义完整性（不跨主题切碎）
- 检索命中时能展示章节来源（`章节：{heading}`）
- 块大小适中（太大会稀释语义，太小会丢失上下文）

## 5. 引用与来源（RAG 的可信度设计）

- 工具返回的检索片段带 `[来源：标题|source_id]` 标记 → LLM 必须基于这些材料回答，末尾标明来源
- 前端渲染引用卡片（project / title / public_summary / public_url）
- 公开引用只用 front matter 中**审核过的** `public_summary` 与 `public_url`，原始 Markdown 与本地路径永不外泄（见 governance-security.md）

## 6. 检索评估（面试增强点，目标态）

给系统建一个小的 QA 评估集（20-30 条"问题 → 期望命中的 source_id"），跑 recall@k：

```bash
# 目标态：eval 脚本输出
recall@5: 0.87   precision@5: 0.72
```

**为什么这是加分项**：90% 的求职者 RAG 项目没有评估闭环。"我给检索系统做了评估集"一句话就区分开了。

## 7. 面试高频追问

**Q: 为什么不直接用向量数据库？**
A: 容量选型。几千 chunk 单机 SQLite 全量扫描毫秒级；pgvector/Milvus 是数据量和并发上来后的迁移路径。演示项目用 SQLite 零依赖、可 Docker 化，复杂度最低。

**Q: 阈值 0.35 怎么定的？**
A: 初始启发式值；正确做法是评估集校准（选 recall/precision 平衡点），这正好是项目的后续增强点。

**Q: embedding 模型是动态下载的？部署怎么处理？**
A: 首次索引下载到 Docker volume 持久化，之后可离线重建（README 已写明）。

**Q: 检索不到怎么办？**
A: 工具返回"未找到直接相关材料"→ persona prompt 强制"资料不足时明确说明，不编造"。诚实比答得漂亮重要——这也是产品信任设计的一部分。
