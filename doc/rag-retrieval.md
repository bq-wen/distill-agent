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

**已知限制（中文）**：FTS5 默认 `unicode61` 分词把连续汉字当整体 token——查询与正文的断词不一致时（如查"工具安全"、正文是"工具调用安全"）关键词召回会落空；英文/数字专有名词（WenGraph、ToolGuard）命中稳定。中文语义召回由向量检索兜底。如需更强中文关键词召回，可换 jieba 分词或 n-gram 索引（增强点）。

## 4. Chunking（分块）

`knowledge/chunking.py`：按 Markdown 标题分块，每块带 heading 上下文，保证：
- 块内有语义完整性（不跨主题切碎）
- 检索命中时能展示章节来源（`章节：{heading}`）
- 块大小适中（太大会稀释语义，太小会丢失上下文）

## 5. 引用与来源（RAG 的可信度设计）

- 工具返回的检索片段带 `[来源：标题|source_id]` 标记 → LLM 必须基于这些材料回答，末尾标明来源
- 前端渲染引用卡片（project / title / public_summary / public_url）
- 公开引用只用 front matter 中**审核过的** `public_summary` 与 `public_url`，原始 Markdown 与本地路径永不外泄（见 governance-security.md）

## 6. 检索评估（已实现：`knowledge/eval.py`）

评估模块把生产检索路径（`PersonalKnowledgeService.search_hybrid`）与两种单路策略对齐量化，指标为标准 IR 指标：

- `recall@k`：top-k 结果覆盖期望来源的比例（命中按 source 去重计数）
- `precision@k`：top-k 结果中命中期来源的比例
- `MRR`：第一个命中的倒排名次

```bash
# 评估集：eval_cases/example.yaml（问题 → 期望命中的 source_id）
python -m personal_agent.knowledge.eval \
  --database data/knowledge.db --cases eval_cases/example.yaml \
  --strategy all --min-score 0.35 --report data/eval-report.json
```

`--strategy all` 同时输出 semantic / keyword / hybrid 三列对比，未命中案例单独列出（供改进检索或提问改写用）。评估与在线服务走同一条 `search_hybrid` 合并逻辑，数字即线上表现。

**当前实测（8 条示例评估集，bge-small-zh-v1.5，阈值 0.35）**：

```
策略          recall@1    recall@3    recall@5    MRR
semantic      0.875       1.000       1.000       0.938
keyword       0.250       0.250       0.250       0.250
hybrid        0.875       1.000       1.000       0.938
```

说明：语料目前只有 profile + placeholder 两个来源，评估集偏语义型；扩充真实资料后需同步扩展评估集并重跑，数字才会反映真实覆盖。关键词侧在中文语义表达上召回弱（FTS5 断词），这是后续提问改写/分词增强的量化基线。

## 6.5 检索选型边界（为什么没有 RRF / rerank / query 改写）

面试被问到"为什么不做 RRF 融合、不做 rerank"时，正确回答不是"不会"，而是**按容量选型**：

- 语料是几千 chunk 的个人知识库，混合检索召回后直接进 prompt，**没有中间结果太多需要重排的问题**——rerank 解决的场景（召回千级、去重排序）在这个规模不存在。
- 融合用的是**按 chunk 去重、语义命中优先**（两路召回取并集，同一 chunk 只留语义分数那份）。RRF 的价值在于多路分数不可比时做排名融合；我们的语义分数有统一阈值（0.35）可直接比较，RRF 属于不必要复杂度。
- 提问改写 / hyDE / 子问题分解解决的是"问题表达与资料不一致"的召回缺口；当前用**强制首轮检索 + ReAct 图内按需二次检索 + 检索收敛提示**缓解，评估数字证实语义路已覆盖大部分表达差异。**下一步若做 query 改写，先以评估集为基线量化改进幅度**（这是评估模块的用途）。
- 结论：单一指标驱动的技术堆砌不成立；评估模块的价值就是给"是否需要加复杂度"提供数字依据。

## 7. 面试高频追问

**Q: 为什么不直接用向量数据库？**
A: 容量选型。几千 chunk 单机 SQLite 全量扫描毫秒级；pgvector/Milvus 是数据量和并发上来后的迁移路径。演示项目用 SQLite 零依赖、可 Docker 化，复杂度最低。

**Q: 阈值 0.35 怎么定的？**
A: 初始启发式值；现在有评估模块（`knowledge/eval.py`）可以跑阈值扫描校准——把评估集上 recall/precision 的平衡点作为阈值依据，而不是拍脑袋。

**Q: embedding 模型是动态下载的？部署怎么处理？**
A: 首次索引下载到 Docker volume 持久化，之后可离线重建（README 已写明）。

**Q: 检索不到怎么办？**
A: 工具返回"未找到直接相关材料"→ persona prompt 强制"资料不足时明确说明，不编造"。诚实比答得漂亮重要——这也是产品信任设计的一部分。
