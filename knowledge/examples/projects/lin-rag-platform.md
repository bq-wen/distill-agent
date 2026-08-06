---
source_id: lin-rag-platform
project: RAG Platform
title: RAG 平台检索组件
visibility: private
public_summary: 面向企业文档问答的 RAG 检索组件：混合检索、阈值控制与引用合同。
public_url: https://example.com/lin/rag-platform
public_questions:
  - RAG 系统怎么保证回答可信？
  - 混合检索里的阈值怎么定？
topics: [RAG, 检索]
---
# RAG 平台检索组件

Lin 负责的检索组件服务企业文档问答，输入是授权过的 Markdown 文档，输出是带引用的回答。

# 混合检索

语义检索（bge 系列 embedding）+ 关键词检索（FTS）双路召回，合并排序后按分数阈值过滤。阈值的作用是"宁可不说、不可乱说"：低于阈值的资料不进入回答上下文。

# 引用合同

回答的引用卡片只来自文档 front matter 里审核过的公开摘要与链接，正文私有内容不出现在 API 返回里。评估方式是每两周跑一轮 30 条 QA 的人工抽检。

# 一个教训

早期只用语义检索，长尾专有名词（内部系统名）召回差，加关键词召回后提升明显。由此总结：单一检索方式的覆盖率都不够，先测 recall 再调排序。
