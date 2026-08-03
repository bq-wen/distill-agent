"""Distillation runner: shared context construction and run/approve flows.

``distill run`` executes the supervised pipeline and pauses at each approval
gate; ``distill approve`` resumes a pending run from its persisted checkpoint,
so review can happen in a separate process.
"""

import asyncio
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from personal_agent.distillation.contracts import RUN_ID_PATTERN, AuditArtifact, DistillState
from personal_agent.distillation.graph import build_distillation_graph
from personal_agent.knowledge.embedding import HashEmbeddingProvider, SentenceTransformersEmbeddingProvider
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore
from personal_agent.wengraph_runtime import (
    ChatModel,
    GraphExecutor,
    OpenAIChatConfig,
    OpenAIChatModel,
    RunResult,
    RunStatus,
    SQLiteArtifactStore,
    SQLiteCheckpointStore,
    SQLiteDatabase,
    SQLiteRunStore,
    SQLiteToolExecutionStore,
    State,
)


@dataclass(slots=True)
class DistillContext:
    """Long-lived resources shared by ``run`` and ``approve`` commands."""

    input_dir: Path
    audit_dir: Path
    distill_dir: Path
    database: SQLiteDatabase
    artifact_store: SQLiteArtifactStore
    checkpoint_store: SQLiteCheckpointStore
    run_store: SQLiteRunStore
    tool_execution_store: SQLiteToolExecutionStore
    knowledge: PersonalKnowledgeService
    knowledge_store: KnowledgeStore
    chat_model: ChatModel
    extraction_prompt: str | None = None

    def close(self) -> None:
        self.database.close()
        self.knowledge_store.close()

    @property
    def state_file(self) -> Path:
        return self.distill_dir / "state.json"

    def load_state(self) -> DistillState:
        if not self.state_file.is_file():
            return DistillState()
        try:
            return DistillState.model_validate_json(self.state_file.read_text(encoding="utf-8"))
        except ValueError:
            return DistillState()

    def save_state(self, state: DistillState) -> None:
        self.distill_dir.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(state.model_dump_json(indent=2), encoding="utf-8")


def build_context(
    *,
    input_dir: str | Path,
    data_dir: str | Path,
    knowledge_database: str | Path,
    embedding_model: str = "BAAI/bge-small-zh-v1.5",
    embedding_device: str | None = "cpu",
    hash_embedding: bool = False,
    chat_model: ChatModel | None = None,
    extraction_prompt: str | None = None,
) -> DistillContext:
    """Create the SQLite-backed stores and knowledge service for one distillation."""

    distill_dir = Path(data_dir) / "distill"
    distill_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = distill_dir / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    database = SQLiteDatabase(distill_dir / "runtime.db")
    knowledge_store = KnowledgeStore(knowledge_database)
    provider = (
        HashEmbeddingProvider()
        if hash_embedding
        else SentenceTransformersEmbeddingProvider(embedding_model, device=embedding_device)
    )
    knowledge = PersonalKnowledgeService(knowledge_store, provider)
    model = chat_model or OpenAIChatModel(OpenAIChatConfig.from_environment())
    return DistillContext(
        input_dir=Path(input_dir),
        audit_dir=audit_dir,
        distill_dir=distill_dir,
        database=database,
        artifact_store=SQLiteArtifactStore(database),
        checkpoint_store=SQLiteCheckpointStore(database),
        run_store=SQLiteRunStore(database),
        tool_execution_store=SQLiteToolExecutionStore(database),
        knowledge=knowledge,
        knowledge_store=knowledge_store,
        chat_model=model,
        extraction_prompt=extraction_prompt,
    )


def _new_executor(ctx: DistillContext, *, run_id: str, only_files: set[str] | None = None) -> GraphExecutor:
    graph, _ = build_distillation_graph(
        artifact_store=ctx.artifact_store,
        chat_model=ctx.chat_model,
        knowledge=ctx.knowledge,
        audit_dir=str(ctx.audit_dir),
        input_dir=str(ctx.input_dir),
        run_id=run_id,
        extraction_prompt=ctx.extraction_prompt,
        only_files=only_files,
    )
    return GraphExecutor(
        graph,
        State(message=f"蒸馏任务开始：{ctx.input_dir}"),
        checkpoint_store=ctx.checkpoint_store,
        run_store=ctx.run_store,
        tool_execution_store=ctx.tool_execution_store,
        max_steps=60,
        max_tool_calls=8,
    )


async def _run_pipeline(ctx: DistillContext, *, run_id: str, approve: bool | None, yes: bool, only_files: set[str] | None = None) -> RunResult:
    executor = _new_executor(ctx, run_id=run_id, only_files=only_files)
    result = await executor.run(run_id=run_id, timeout_seconds=1800)
    while result.status is RunStatus.PENDING_APPROVAL:
        assert result.checkpoint is not None
        _print_pending(ctx, result)
        if yes:
            approved = True
        elif approve is True:
            approved = True
        elif approve is False:
            approved = False
        elif sys.stdin.isatty():
            approved = _ask("批准此写入？")
        else:
            print(
                f"\nRun {run_id} 等待审批。"
                f"审查 data/distill/audit/{run_id}.json（批准后生成）后执行：\n"
                f"  python -m personal_agent.distillation.cli approve --run {run_id} [--reject]",
                file=sys.stderr,
            )
            return result
        result = await executor.resume(run_id, approved)
    return result


def _print_pending(ctx: DistillContext, result: RunResult) -> None:
    assert result.checkpoint is not None
    message = result.checkpoint.state.message or "等待审批"
    print(f"\n▶ {message}", file=sys.stderr)
    audit_ref = next((ref for ref in result.checkpoint.state.artifacts.values() if ref.kind == "audit"), None)
    if audit_ref is not None:
        try:
            payload = AuditArtifact.model_validate_json(ctx.artifact_store.get_text(audit_ref.artifact_id))
            for atom in payload.atoms[:5]:
                preview = atom.content.split("\n", 1)[0]
                print(f"  · [{atom.kind}] {preview[:80]}", file=sys.stderr)
            if len(payload.atoms) > 5:
                print(f"  · … 共 {len(payload.atoms)} 个原子", file=sys.stderr)
        except (ValueError, KeyError):
            pass


def _ask(prompt: str) -> bool:
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _changed_files(ctx: DistillContext) -> set[str]:
    """Relative paths of input files whose content hash differs from the last run."""

    state = ctx.load_state()
    changed: set[str] = set()
    for path in sorted(ctx.input_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".json"}:
            continue
        relative = str(path.relative_to(ctx.input_dir))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not state.is_unchanged(relative, digest):
            changed.add(relative)
    return changed


def _noop_result(run_id: str, message: str) -> RunResult:
    return RunResult(status=RunStatus.COMPLETED, state=State(message=message))


def _validate_run_id(run_id: str) -> None:
    """Run ID 会用作 SQLite 主键与 audit 文件名，只允许安全字符。"""

    if not re.fullmatch(RUN_ID_PATTERN, run_id):
        raise ValueError(
            f"非法 Run ID: {run_id!r}；只能包含字母、数字、-、_，且以字母或数字开头（最长 128 字符）"
        )


def run_pipeline(
    ctx: DistillContext,
    *,
    run_id: str | None = None,
    yes: bool = False,
    incremental: bool = False,
) -> RunResult:
    """Execute the full distillation pipeline with approval gates."""

    run_id = run_id or f"distill-{uuid4()}"
    _validate_run_id(run_id)
    only_files: set[str] | None = None
    if incremental:
        only_files = _changed_files(ctx)
        if not only_files:
            print("没有内容变化的文件，跳过本次蒸馏。", file=sys.stderr)
            return _noop_result(run_id, "没有内容变化的文件，跳过本次蒸馏。")
    result = asyncio.run(_run_pipeline(ctx, run_id=run_id, approve=None, yes=yes, only_files=only_files))
    if result.status is RunStatus.COMPLETED:
        _record_indexed_hashes(ctx, run_id)
    return result


def approve_run(ctx: DistillContext, *, run_id: str, approved: bool) -> RunResult:
    """Resume a pending run from its persisted checkpoint."""

    _validate_run_id(run_id)
    executor = _new_executor(ctx, run_id=run_id)
    result = asyncio.run(executor.resume(run_id, approved))
    while result.status is RunStatus.PENDING_APPROVAL:
        assert result.checkpoint is not None
        _print_pending(ctx, result)
        result = asyncio.run(executor.resume(run_id, approved))
    if result.status is RunStatus.COMPLETED:
        _record_indexed_hashes(ctx, run_id)
    return result


def _record_indexed_hashes(ctx: DistillContext, run_id: str) -> None:
    """Record content hashes of every input file after a successful run.

    Only files that actually produced indexed atoms are guaranteed to be in the
    audit artifact, so the incremental skip set is derived from the input
    directory directly instead.
    """

    state = ctx.load_state()
    for path in sorted(ctx.input_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json"}:
            relative = str(path.relative_to(ctx.input_dir))
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            state.files[relative] = digest
    ctx.save_state(state)


def summarize_run(result: RunResult) -> str:
    """Human-readable one-line summary for CLI output."""

    if result.status is RunStatus.COMPLETED:
        return f"蒸馏完成：{result.state.message}"
    if result.status is RunStatus.PENDING_APPROVAL:
        return f"蒸馏等待审批：{result.state.message}"
    return f"蒸馏未完成（{result.status.value}）：{result.error_message or result.state.message}"
