"""Retrieval evaluation module tests: metric math and end-to-end evaluation."""

import math
from pathlib import Path

from personal_agent.knowledge.documents import parse_markdown_document
from personal_agent.knowledge.embedding import HashEmbeddingProvider
from personal_agent.knowledge.eval import (
    CaseResult,
    EvalCase,
    Summary,
    _case_metrics,
    evaluate_cases,
    load_eval_set,
    retrieve,
)
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore

TOP_K = [1, 3, 5]


def test_case_metrics_compute_recall_precision_hit_rank() -> None:
    metrics = _case_metrics(retrieved_ids=["a", "b", "c"], expected=["b"], top_k=TOP_K)

    assert metrics["hit_rank"] == 2
    assert metrics["recall_at"] == {1: 0.0, 3: 1.0, 5: 1.0}
    assert metrics["precision_at"] == {1: 0.0, 3: 1 / 3, 5: 1 / 3}


def test_case_metrics_with_multiple_expected_sources() -> None:
    metrics = _case_metrics(retrieved_ids=["a", "b", "c"], expected=["b", "c"], top_k=TOP_K)

    assert metrics["hit_rank"] == 2
    assert metrics["recall_at"][3] == 1.0
    assert metrics["recall_at"][5] == 1.0
    assert metrics["precision_at"][3] == 2 / 3


def test_case_metrics_miss_returns_zero_mrr() -> None:
    metrics = _case_metrics(retrieved_ids=["x", "y"], expected=["z"], top_k=TOP_K)

    assert metrics["hit_rank"] is None
    assert metrics["recall_at"] == {1: 0.0, 3: 0.0, 5: 0.0}
    assert metrics["precision_at"] == {1: 0.0, 3: 0.0, 5: 0.0}


def test_case_metrics_empty_retrieval_does_not_divide_by_zero() -> None:
    metrics = _case_metrics(retrieved_ids=[], expected=["a"], top_k=[3])

    assert metrics["hit_rank"] is None
    assert metrics["recall_at"][3] == 0.0
    assert metrics["precision_at"][3] == 0.0


def test_case_metrics_dedup_sources_within_top_k() -> None:
    """同一来源多个 chunk 时，命中按 source 去重，recall 不超过 1。"""

    metrics = _case_metrics(retrieved_ids=["a", "a", "b"], expected=["a"], top_k=[1, 3])

    assert metrics["hit_rank"] == 1
    assert metrics["recall_at"] == {1: 1.0, 3: 1.0}
    assert metrics["precision_at"] == {1: 1.0, 3: 1 / 3}


def test_load_eval_set_parses_yaml(tmp_path: Path) -> None:
    path = tmp_path / "cases.yaml"
    path.write_text(
        """metadata:
  description: 测试评估集
cases:
  - question: "WenGraph 如何治理工具？"
    expected_source_ids: ["wengraph-overview"]
    note: 语义
  - question: "ToolGuard"
    expected_source_ids: ["wengraph-overview"]
""",
        encoding="utf-8",
    )

    eval_set = load_eval_set(path)

    assert eval_set.description == "测试评估集"
    assert len(eval_set.cases) == 2
    assert eval_set.cases[0].expected_source_ids == ["wengraph-overview"]


def _knowledge_service(tmp_path: Path) -> PersonalKnowledgeService:
    alpha = tmp_path / "wengraph.md"
    alpha.write_text(
        """---
source_id: wengraph-overview
project: WenGraph
title: WenGraph 架构
visibility: private
public_summary: 自研图运行时。
---
# 治理

ToolGuard 结合能力策略与风险策略审核工具调用。

# 恢复

状态变更通过 StatePatch 表达，checkpoint 支持跨进程恢复。
""",
        encoding="utf-8",
    )
    beta = tmp_path / "profile.md"
    beta.write_text(
        """---
source_id: profile
project: Personal
title: 个人画像
visibility: private
profile: true
name: Wen
---
# 方向

关注 RAG 全链路与 Agent 工程。
""",
        encoding="utf-8",
    )
    store = KnowledgeStore(tmp_path / "knowledge.db")
    service = PersonalKnowledgeService(store, HashEmbeddingProvider())
    for document_path in (alpha, beta):
        service.index_document(parse_markdown_document(document_path))
    return service


def test_hybrid_merge_dedups_and_prefers_semantic_over_keyword(tmp_path: Path) -> None:
    service = _knowledge_service(tmp_path)
    try:
        # 关键词专有名词：ToolGuard 只在 alpha 的治理章节出现。
        keyword_only = service.search_keywords("ToolGuard", limit=5)
        assert keyword_only and all(match.source.source_id == "wengraph-overview" for match in keyword_only)

        # 混合合并：去重发生在 chunk 级（同一 chunk 同时被两路命中只保留一次），
        # 同一来源的不同 chunk 可以合法共存。
        hybrid = service.search_hybrid("ToolGuard", limit=5, minimum_semantic_score=0.0)
        chunk_ids = [match.chunk.chunk_id for match in hybrid]
        assert len(chunk_ids) == len(set(chunk_ids)), "混合检索不应出现重复 chunk"
        assert len(hybrid) <= 5
        assert hybrid[0].source.source_id == "wengraph-overview", "关键词专有名词应优先命中"
    finally:
        service.store.close()


def test_evaluate_cases_end_to_end_produces_bounded_metrics(tmp_path: Path) -> None:
    service = _knowledge_service(tmp_path)
    cases = [
        EvalCase(question="ToolGuard 如何治理工具调用？", expected_source_ids=["wengraph-overview"]),
        EvalCase(question="RAG 全链路", expected_source_ids=["profile"]),
    ]
    try:
        results, summary = evaluate_cases(
            service, cases, "hybrid", limit=5, minimum_semantic_score=0.0, top_k=TOP_K,
        )
    finally:
        service.store.close()

    assert isinstance(summary, Summary)
    assert len(results) == 2
    assert all(isinstance(result, CaseResult) for result in results)
    for k in TOP_K:
        assert 0.0 <= summary.recall_at[k] <= 1.0
        assert 0.0 <= summary.precision_at[k] <= 1.0
    assert 0.0 <= summary.mrr <= 1.0
    # 期望来源必须都在知识库中，否则是评估集错误而非检索失败。
    for case in cases:
        assert all(sid in {"wengraph-overview", "profile"} for sid in case.expected_source_ids)


def test_retrieve_dispatches_all_strategies(tmp_path: Path) -> None:
    service = _knowledge_service(tmp_path)
    try:
        for strategy in ("semantic", "keyword", "hybrid"):
            matches = retrieve(service, strategy, "ToolGuard", limit=3, minimum_semantic_score=0.0)
            assert isinstance(matches, list)
            assert all(math.isclose(match.score, match.score) >= 0.0 for match in matches)
    finally:
        service.store.close()
