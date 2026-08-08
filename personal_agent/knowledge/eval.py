"""Retrieval evaluation for the personal knowledge base.

Quantifies the retrieval capability of the production path
(``PersonalKnowledgeService.search_hybrid`` and its two single-strategy sides)
with standard information-retrieval metrics over a small labeled QA set:

- ``recall@k``:  top-k 结果覆盖期望来源的比例（|hits ∩ expected| / |expected|）
- ``precision@k``: top-k 结果中命中期来源的比例（|hits| / k）
- ``MRR``: 第一个命中的倒排名次（无命中记 0）

Usage::

    python -m personal_agent.knowledge.eval \\
        --database data/knowledge.db --cases eval_cases/example.yaml \\
        --strategy all --min-score 0.35 --report data/eval-report.json

Cases are YAML::

    metadata:
      description: ...
    cases:
      - question: "WenGraph 如何治理工具调用？"
        expected_source_ids: ["wengraph-overview"]
        note: 可选备注
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from personal_agent.knowledge.embedding import (
    HashEmbeddingProvider,
    SentenceTransformersEmbeddingProvider,
)
from personal_agent.knowledge.models import RetrievalMatch
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore

Strategy = Literal["semantic", "keyword", "hybrid"]


class EvalCase(BaseModel):
    """One labeled retrieval question; the expected set is evaluated at source level."""

    question: str = Field(min_length=1, max_length=500)
    expected_source_ids: list[str] = Field(min_length=1, max_length=10)
    note: str | None = None


class EvalSet(BaseModel):
    """Loaded evaluation set with description and cases."""

    description: str = ""
    cases: list[EvalCase] = Field(min_length=1)


@dataclass(slots=True)
class CaseResult:
    """Per-case retrieval outcome, source ids in returned order (1-based rank)."""

    question: str
    expected: list[str]
    retrieved: list[str]
    hit_rank: int | None  # 1-based rank of first hit, None when missed
    strategy: Strategy


@dataclass(slots=True)
class Summary:
    """Aggregated metrics over all cases for one strategy."""

    strategy: Strategy
    case_count: int
    recall_at: dict[int, float] = field(default_factory=dict)
    precision_at: dict[int, float] = field(default_factory=dict)
    mrr: float = 0.0


def load_eval_set(path: str | Path) -> EvalSet:
    raw = Path(path).read_text(encoding="utf-8")
    payload = yaml.safe_load(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"评估集必须是对象: {path}")
    metadata = payload.get("metadata")
    description = metadata.get("description", "") if isinstance(metadata, dict) else ""
    return EvalSet(description=description, cases=payload["cases"])


def retrieve(
    service: PersonalKnowledgeService,
    strategy: Strategy,
    question: str,
    *,
    limit: int,
    minimum_semantic_score: float,
) -> list[RetrievalMatch]:
    if strategy == "semantic":
        return service.search_semantic(question, limit=limit)
    if strategy == "keyword":
        return service.search_keywords(question, limit=limit)
    return service.search_hybrid(
        question, limit=limit, minimum_semantic_score=minimum_semantic_score
    )


def _case_metrics(retrieved_ids: list[str], expected: list[str], top_k: list[int]) -> dict:
    """Pure per-case metric computation; retrieved ids are in 1-based rank order."""

    expected_set = set(expected)
    first_hit = next(
        (position for position, sid in enumerate(retrieved_ids, start=1) if sid in expected_set),
        None,
    )
    recall_at: dict[int, float] = {}
    precision_at: dict[int, float] = {}
    for k in top_k:
        ids_at_k = retrieved_ids[:k]
        # 命中按 source 去重：同一来源的多个 chunk 在 top-k 中只计一次。
        hits = len(set(ids_at_k) & expected_set)
        recall_at[k] = hits / len(expected_set)
        precision_at[k] = hits / min(k, len(ids_at_k)) if ids_at_k else 0.0
    return {"recall_at": recall_at, "precision_at": precision_at, "hit_rank": first_hit}


def evaluate_cases(
    service: PersonalKnowledgeService,
    cases: list[EvalCase],
    strategy: Strategy,
    *,
    limit: int,
    minimum_semantic_score: float,
    top_k: list[int],
) -> tuple[list[CaseResult], Summary]:
    results: list[CaseResult] = []
    recall: dict[int, list[float]] = {k: [] for k in top_k}
    precision: dict[int, list[float]] = {k: [] for k in top_k}
    mrr_values: list[float] = []

    for case in cases:
        matches = retrieve(
            service,
            strategy,
            case.question,
            limit=limit,
            minimum_semantic_score=minimum_semantic_score,
        )
        retrieved_ids = [match.source.source_id for match in matches]
        metrics = _case_metrics(retrieved_ids, case.expected_source_ids, top_k)
        results.append(
            CaseResult(
                question=case.question,
                expected=case.expected_source_ids,
                retrieved=retrieved_ids,
                hit_rank=metrics["hit_rank"],
                strategy=strategy,
            )
        )
        mrr_values.append(1.0 / metrics["hit_rank"] if metrics["hit_rank"] else 0.0)
        for k in top_k:
            recall[k].append(metrics["recall_at"][k])
            precision[k].append(metrics["precision_at"][k])

    summary = Summary(strategy=strategy, case_count=len(cases))
    summary.recall_at = {k: _mean(values) for k, values in recall.items()}
    summary.precision_at = {k: _mean(values) for k, values in precision.items()}
    summary.mrr = _mean(mrr_values)
    return results, summary


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _report_payload(
    results: list[CaseResult],
    summaries: list[Summary],
    *,
    limit: int,
    top_k: list[int],
    minimum_semantic_score: float,
) -> dict:
    return {
        "limit": limit,
        "top_k": top_k,
        "minimum_semantic_score": minimum_semantic_score,
        "summaries": [
            {
                "strategy": s.strategy,
                "case_count": s.case_count,
                "recall@k": {str(k): round(v, 4) for k, v in sorted(s.recall_at.items())},
                "precision@k": {str(k): round(v, 4) for k, v in sorted(s.precision_at.items())},
                "mrr": round(s.mrr, 4),
            }
            for s in summaries
        ],
        "cases": [
            {
                "question": r.question,
                "expected_source_ids": r.expected,
                "retrieved_source_ids": r.retrieved,
                "hit_rank": r.hit_rank,
                "strategy": r.strategy,
            }
            for r in results
        ],
    }


def _render_table(summaries: list[Summary], top_k: list[int]) -> str:
    header = f"{'策略':<10}" + "".join(f"{'recall@' + str(k):>12}" for k in top_k) + f"{'MRR':>10}"
    lines = [header, "-" * len(header)]
    for summary in summaries:
        row = f"{summary.strategy:<10}"
        row += "".join(f"{summary.recall_at.get(k, 0.0):>12.3f}" for k in top_k)
        row += f"{summary.mrr:>10.3f}"
        lines.append(row)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="knowledge-eval",
        description="评估个人知识检索能力（recall@k / precision@k / MRR）",
    )
    parser.add_argument("--database", required=True, help="知识库 SQLite 路径")
    parser.add_argument("--cases", required=True, help="评估集 YAML 路径")
    parser.add_argument(
        "--strategy",
        choices=["semantic", "keyword", "hybrid", "all"],
        default="hybrid",
        help="评估策略；all 同时输出三种对比（推荐）",
    )
    parser.add_argument("--top-k", default="1,3,5", help="逗号分隔的 top-k 列表")
    parser.add_argument("--limit", type=int, default=5, help="每次检索返回条数（应 >= max top-k）")
    parser.add_argument(
        "--min-score", type=float, default=0.0, help="语义阈值（hybrid 生效；生产 0.35）"
    )
    parser.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--device", default="cpu")
    parser.add_argument(
        "--hash-embedding", action="store_true", help="仅用于测试：哈希向量无语义，指标无意义"
    )
    parser.add_argument("--report", default=None, help="报告输出 JSON 路径")
    args = parser.parse_args(argv)

    top_k = [int(value.strip()) for value in args.top_k.split(",") if value.strip()]
    if not top_k or any(k < 1 for k in top_k):
        parser.error("--top-k 必须是正整数列表")
    if args.limit < max(top_k):
        parser.error(
            f"--limit（{args.limit}）应 >= max top-k（{max(top_k)}），否则高 k 值按实际返回计算"
        )

    eval_set = load_eval_set(args.cases)
    store = KnowledgeStore(args.database)
    provider = (
        HashEmbeddingProvider()
        if args.hash_embedding
        else SentenceTransformersEmbeddingProvider(args.embedding_model, device=args.device)
    )
    service = PersonalKnowledgeService(store, provider)
    strategies: list[Strategy] = (
        ["semantic", "keyword", "hybrid"] if args.strategy == "all" else [args.strategy]  # type: ignore[list-item]
    )
    try:
        summaries: list[Summary] = []
        all_results: list[CaseResult] = []
        for strategy in strategies:
            results, summary = evaluate_cases(
                service,
                eval_set.cases,
                strategy,
                limit=args.limit,
                minimum_semantic_score=args.min_score,
                top_k=top_k,
            )
            all_results.extend(results)
            summaries.append(summary)
    finally:
        store.close()

    print(f"评估集: {args.cases}")
    print(f"案例数: {len(eval_set.cases)} ｜ limit={args.limit} ｜ 语义阈值={args.min_score}")
    if eval_set.description:
        print(f"说明: {eval_set.description}")
    print()
    print(_render_table(summaries, top_k))
    print("\n=== 未命中案例（供改进检索/提问改写用）===")
    missed = [r for r in all_results if r.hit_rank is None]
    for result in missed:
        print(f"  ✗ [{result.strategy}] {result.question}")
        print(f"      期望: {result.expected}  实际 top: {result.retrieved or '无'}")
    if not missed:
        print("  （全部命中）")

    if args.report:
        payload = _report_payload(
            all_results,
            summaries,
            limit=args.limit,
            top_k=top_k,
            minimum_semantic_score=args.min_score,
        )
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入: {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
