"""CLI entry point for the knowledge distillation pipeline.

Examples::

    python -m personal_agent.distillation.cli run --input knowledge/examples/raw --database data/knowledge.db
    python -m personal_agent.distillation.cli run --input knowledge/examples/raw --database data/knowledge.db --yes
    python -m personal_agent.distillation.cli approve --run distill-xxxx --database data/knowledge.db --reject
"""

import argparse
import os
import sys
from pathlib import Path

from personal_agent.distillation.runner import (
    approve_run,
    build_context,
    run_pipeline,
    summarize_run,
)
from personal_agent.settings import ApplicationSettings


def _settings() -> ApplicationSettings:
    return ApplicationSettings.from_environment()


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="distill")
    parser.add_argument("--data-dir", type=Path, default=None, help="运行时数据目录（默认取 PERSONAL_AGENT_DATA_DIR）")
    parser.add_argument("--database", type=Path, default=None, help="知识库 SQLite 路径（默认 data/knowledge.db）")
    parser.add_argument("--hash-embedding", action="store_true", help="使用哈希向量（仅供测试，不用于生产检索）")
    parser.add_argument("--embedding-model", default="BAAI/bge-small-zh-v1.5", help="本地 embedding 模型")
    parser.add_argument("--device", default="cpu", help="embedding 推理设备（cpu/cuda）")
    return parser


def _resolve_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    settings = _settings()
    data_dir = args.data_dir or settings.data_directory
    database = args.database or settings.knowledge_database
    return data_dir, database


def cmd_run(args: argparse.Namespace) -> int:
    data_dir, database = _resolve_paths(args)
    ctx = build_context(
        input_dir=args.input,
        data_dir=data_dir,
        knowledge_database=database,
        embedding_model=args.embedding_model,
        embedding_device=args.device,
        hash_embedding=args.hash_embedding,
    )
    try:
        result = run_pipeline(ctx, run_id=args.run_id, yes=args.yes, incremental=args.incremental)
    finally:
        ctx.close()
    print(summarize_run(result))
    return 0 if result.status.value in {"completed", "pending_approval"} else 1


def cmd_approve(args: argparse.Namespace) -> int:
    data_dir, database = _resolve_paths(args)
    ctx = build_context(
        input_dir=args.input or Path(os.getcwd()),
        data_dir=data_dir,
        knowledge_database=database,
        embedding_model=args.embedding_model,
        embedding_device=args.device,
        hash_embedding=args.hash_embedding,
    )
    try:
        result = approve_run(ctx, run_id=args.run, approved=not args.reject)
    finally:
        ctx.close()
    print(summarize_run(result))
    return 0 if result.status.value == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="distill", description="个人知识蒸馏流水线")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="运行完整蒸馏流水线（含人工审批闸门）")
    run_parser.add_argument("--input", type=Path, required=True, help="原始资料目录（.md/.txt/.json）")
    run_parser.add_argument("--run-id", default=None, help="自定义 Run ID")
    run_parser.add_argument("--yes", action="store_true", help="自动批准所有审批闸门（CI/演示用）")
    run_parser.add_argument("--incremental", action="store_true", help="增量模式（跳过内容未变化的文件）")
    _attach_common(run_parser, _common_parser())

    approve_parser = subparsers.add_parser("approve", help="批准/驳回等待审批的蒸馏 Run")
    approve_parser.add_argument("--run", required=True, help="Run ID（见 run 输出）")
    approve_parser.add_argument("--reject", action="store_true", help="驳回而非批准")
    approve_parser.add_argument("--input", type=Path, default=None, help="原始资料目录（恢复图所需）")
    _attach_common(approve_parser, _common_parser())

    args = parser.parse_args(argv)
    handler = cmd_run if args.command == "run" else cmd_approve
    return handler(args)


def _attach_common(subparser: argparse.ArgumentParser, common: argparse.ArgumentParser) -> None:
    for action in common._actions[1:]:
        subparser._add_action(action)


if __name__ == "__main__":
    sys.exit(main())
