import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from personal_agent.distillation.contracts import AuditArtifact, DistillState
from personal_agent.distillation.graph import build_distillation_graph
from personal_agent.distillation.runner import approve_run, build_context, run_pipeline
from personal_agent.distillation.tools import WriteAuditArtifactTool
from personal_agent.knowledge.embedding import HashEmbeddingProvider
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore
from personal_agent.wengraph_runtime import (
    ChatModel,
    GraphExecutor,
    ModelResponse,
    RunStatus,
    SQLiteArtifactStore,
    SQLiteCheckpointStore,
    SQLiteDatabase,
    SQLiteRunStore,
    SQLiteToolExecutionStore,
    State,
)


class ScriptedDistillModel(ChatModel):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.messages = []

    async def complete(self, messages, tools) -> ModelResponse:
        self.messages.append((messages, tools))
        return self.responses.pop(0)


def _atoms_response(*contents: str) -> ModelResponse:
    atoms = [
        {"content": content, "kind": "statement", "confidence": 0.9, "source_type": "notes"}
        for content in contents
    ]
    return ModelResponse(text=json.dumps({"atoms": atoms}, ensure_ascii=False))


def _write_raw_materials(directory: Path) -> Path:
    source = directory / "project" / "readme.md"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "# 项目说明\n\n我开发了 ToolGuard 来治理 Agent 工具调用。\n\n# 架构\n\n状态用 StatePatch 表达变更。",
        encoding="utf-8",
    )
    chat = directory / "chat" / "notes.txt"
    chat.parent.mkdir(parents=True, exist_ok=True)
    chat.write_text(
        "20:30 - 有人问过：为什么不用 LangChain？\n我回答：可控性，安全决策内建到图里。\n",
        encoding="utf-8",
    )
    return directory


def _scripted_context(tmp_path: Path, model: ChatModel):
    inputs = _write_raw_materials(tmp_path / "raw")
    return build_context(
        input_dir=inputs,
        data_dir=tmp_path / "data",
        knowledge_database=tmp_path / "data" / "knowledge.db",
        hash_embedding=True,
        chat_model=model,
    )


def test_run_id_whitelist_blocks_path_traversal(tmp_path: Path) -> None:
    """run_id 拼进 audit 文件名：非法字符必须在入口、参数模型与工具三层被拦截。"""

    # 契约层：AuditArtifact 拒绝含路径分隔符的 run_id。
    with pytest.raises(ValidationError):
        AuditArtifact(
            run_id="../../etc/passwd",
            created_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        )

    # 工具层：即使绕过参数模型，execute 也不会把文件写到 audit 目录之外。
    audit_dir = tmp_path / "audit"
    tool = WriteAuditArtifactTool(audit_dir)

    class BadArgs:
        run_id = "../../escape"
        audit_json = '{"run_id":"../../escape"}'

    with pytest.raises(ValueError, match="非法 Run ID"):
        asyncio.run(tool.execute(BadArgs()))
    assert not (tmp_path / "escape.json").exists()

    # runner 入口：run 与 approve 都拒绝非法 run_id，错误信息清晰。
    model = ScriptedDistillModel([_atoms_response("我用 ToolGuard 治理工具调用。")])
    ctx = _scripted_context(tmp_path, model)
    try:
        with pytest.raises(ValueError, match="非法 Run ID"):
            run_pipeline(ctx, run_id="../evil", yes=True)
        with pytest.raises(ValueError, match="非法 Run ID"):
            approve_run(ctx, run_id="../../evil", approved=True)
    finally:
        ctx.close()


def test_pipeline_pauses_for_approval_and_indexes_after_approve(tmp_path: Path) -> None:
    model = ScriptedDistillModel(
        [
            _atoms_response("我用 ToolGuard 治理 Agent 工具调用。", "状态变更通过 StatePatch 表达。"),
            _atoms_response("我选择自研图运行时是因为可控性。"),
        ]
    )
    ctx = _scripted_context(tmp_path, model)
    try:
        run_id = "distill-test-1"
        graph, _ = build_distillation_graph(
            artifact_store=ctx.artifact_store,
            chat_model=ctx.chat_model,
            knowledge=ctx.knowledge,
            audit_dir=str(ctx.audit_dir),
            input_dir=str(ctx.input_dir),
            run_id=run_id,
        )
        executor = GraphExecutor(
            graph,
            State(message="蒸馏任务开始"),
            checkpoint_store=ctx.checkpoint_store,
            run_store=ctx.run_store,
            tool_execution_store=ctx.tool_execution_store,
            max_steps=60,
            max_tool_calls=8,
        )
        result = asyncio.run(executor.run(run_id=run_id, timeout_seconds=120))

        # 审批闸门触发：第一次暂停在写审计产物。
        assert result.status is RunStatus.PENDING_APPROVAL
        assert result.checkpoint is not None
        assert result.checkpoint.state.pending_tool_requests[0].tool_name == "write_audit_artifact"

        result = asyncio.run(executor.resume(run_id, True))
        assert result.status is RunStatus.PENDING_APPROVAL
        assert result.checkpoint.state.pending_tool_requests[0].tool_name == "index_documents"

        result = asyncio.run(executor.resume(run_id, True))
        assert result.status is RunStatus.COMPLETED

        # 审计产物落盘、知识库可检索、前端 topics 能看到。
        assert (ctx.audit_dir / f"{run_id}.json").is_file()
        assert len(ctx.knowledge_store.list_sources()) == 2
        assert ctx.knowledge.search_keywords("ToolGuard")
        assert ctx.knowledge.search_semantic("工具调用治理")
    finally:
        ctx.close()


def test_rejected_run_writes_nothing(tmp_path: Path) -> None:
    model = ScriptedDistillModel(
        [_atoms_response("我用 ToolGuard 治理 Agent 工具调用。"), _atoms_response("状态变更通过 StatePatch 表达。")]
    )
    ctx = _scripted_context(tmp_path, model)
    try:
        run_id = "distill-test-reject"
        graph, _ = build_distillation_graph(
            artifact_store=ctx.artifact_store,
            chat_model=ctx.chat_model,
            knowledge=ctx.knowledge,
            audit_dir=str(ctx.audit_dir),
            input_dir=str(ctx.input_dir),
            run_id=run_id,
        )
        executor = GraphExecutor(
            graph,
            State(message="蒸馏任务开始"),
            checkpoint_store=ctx.checkpoint_store,
            run_store=ctx.run_store,
            tool_execution_store=ctx.tool_execution_store,
            max_steps=60,
            max_tool_calls=8,
        )
        result = asyncio.run(executor.run(run_id=run_id, timeout_seconds=120))
        assert result.status is RunStatus.PENDING_APPROVAL

        result = asyncio.run(executor.resume(run_id, False))
        assert result.status is RunStatus.COMPLETED
        assert "中止" in (result.state.message or "")

        assert not (ctx.audit_dir / f"{run_id}.json").exists()
        assert ctx.knowledge_store.list_sources() == []
        assert not ctx.state_file.exists()
    finally:
        ctx.close()


def test_approve_command_resumes_detached_run_in_new_context(tmp_path: Path) -> None:
    model = ScriptedDistillModel(
        [_atoms_response("我用 ToolGuard 治理工具调用。"), _atoms_response("我自研图运行时为了可控性。")]
    )
    first = _scripted_context(tmp_path, model)
    run_id = "distill-test-detached"
    try:
        result = asyncio.run(_first_pause(first, run_id))
        assert result.status is RunStatus.PENDING_APPROVAL
    finally:
        first.close()

    # 模拟新的进程：重建上下文（同一 data/knowledge 路径、同一脚本模型），执行 approve。
    second = _scripted_context(tmp_path, model)
    try:
        result = approve_run(second, run_id=run_id, approved=True)
        assert result.status is RunStatus.PENDING_APPROVAL
        assert result.checkpoint is not None
        assert result.checkpoint.state.pending_tool_requests[0].tool_name == "index_documents"
        result = approve_run(second, run_id=run_id, approved=True)
        assert result.status is RunStatus.COMPLETED
        assert len(second.knowledge_store.list_sources()) == 2
    finally:
        second.close()


async def _first_pause(ctx, run_id: str):
    graph, _ = build_distillation_graph(
        artifact_store=ctx.artifact_store,
        chat_model=ctx.chat_model,
        knowledge=ctx.knowledge,
        audit_dir=str(ctx.audit_dir),
        input_dir=str(ctx.input_dir),
        run_id=run_id,
    )
    executor = GraphExecutor(
        graph,
        State(message="蒸馏任务开始"),
        checkpoint_store=ctx.checkpoint_store,
        run_store=ctx.run_store,
        tool_execution_store=ctx.tool_execution_store,
        max_steps=60,
        max_tool_calls=8,
    )
    return await executor.run(run_id=run_id, timeout_seconds=120)


def test_run_pipeline_cli_wrapper_with_yes_flag(tmp_path: Path) -> None:
    model = ScriptedDistillModel(
        [_atoms_response("我用 ToolGuard 治理工具调用。"), _atoms_response("状态变更通过 StatePatch 表达。")]
    )
    ctx = _scripted_context(tmp_path, model)
    try:
        result = run_pipeline(ctx, run_id="distill-test-yes", yes=True)
        assert result.status is RunStatus.COMPLETED
        assert (ctx.audit_dir / "distill-test-yes.json").is_file()
        assert len(ctx.knowledge_store.list_sources()) >= 1
    finally:
        ctx.close()


def test_pipeline_without_atoms_stops_before_gates(tmp_path: Path) -> None:
    model = ScriptedDistillModel([ModelResponse(text="没有可用信息"), ModelResponse(text="没有可用信息")])
    ctx = _scripted_context(tmp_path, model)
    try:
        result = run_pipeline(ctx, run_id="distill-test-empty", yes=True)
        assert result.status is RunStatus.COMPLETED
        assert ctx.knowledge_store.list_sources() == []
    finally:
        ctx.close()


def test_incremental_run_skips_unchanged_files(tmp_path: Path) -> None:
    model = ScriptedDistillModel(
        [
            _atoms_response("我用 ToolGuard 治理工具调用。", "状态变更通过 StatePatch 表达。"),
            _atoms_response("我自研图运行时为了可控性。"),
            _atoms_response("RAG 可信靠混合检索、阈值与引用合同。"),
        ]
    )
    ctx = _scripted_context(tmp_path, model)
    try:
        first = run_pipeline(ctx, run_id="distill-incr-1", yes=True)
        assert first.status is RunStatus.COMPLETED
        assert len(ctx.knowledge_store.list_sources()) == 2
        assert (ctx.distill_dir / "state.json").is_file()

        # 内容未变化：增量运行应直接跳过，不产生新审计产物、不再调用模型。
        second = run_pipeline(ctx, run_id="distill-incr-2", yes=True, incremental=True)
        assert second.status is RunStatus.COMPLETED
        assert "没有内容变化" in (second.state.message or "")
        assert not (ctx.audit_dir / "distill-incr-2.json").exists()
        assert len(model.messages) == 2  # 两次提取调用来自首次运行

        # 修改一个文件后：增量只处理变更文件（一次提取调用）。
        (ctx.input_dir / "chat" / "notes.txt").write_text(
            "20:31 - 有人问：RAG 怎么保证可信？\n我回答：混合检索 + 阈值 + 引用合同。\n",
            encoding="utf-8",
        )
        third = run_pipeline(ctx, run_id="distill-incr-3", yes=True, incremental=True)
        assert third.status is RunStatus.COMPLETED
        assert len(model.messages) == 3
        assert (ctx.audit_dir / "distill-incr-3.json").is_file()
    finally:
        ctx.close()


def test_incremental_run_removes_deleted_source_and_keeps_state_current(tmp_path: Path) -> None:
    model = ScriptedDistillModel(
        [
            _atoms_response("我实现了可审计的知识蒸馏。"),
            _atoms_response("我用混合检索回答问题。"),
        ]
    )
    ctx = _scripted_context(tmp_path, model)
    try:
        first = run_pipeline(ctx, run_id="distill-delete-1", yes=True)
        assert first.status is RunStatus.COMPLETED
        assert len(ctx.knowledge_store.list_sources()) == 2
        removed = ctx.input_dir / "chat" / "notes.txt"
        removed.unlink()

        second = run_pipeline(ctx, run_id="distill-delete-2", yes=True, incremental=True)
        assert second.status is RunStatus.COMPLETED
        assert len(ctx.knowledge_store.list_sources()) == 1
        state = DistillState.model_validate_json(ctx.state_file.read_text(encoding="utf-8"))
        assert set(state.files) == {"project/readme.md"}
    finally:
        ctx.close()


def test_legacy_incremental_state_migrates_without_duplicate_source(tmp_path: Path) -> None:
    model = ScriptedDistillModel([
        _atoms_response("我有一条可迁移的知识。"),
        _atoms_response("我有另一条可迁移的知识。"),
    ])
    ctx = _scripted_context(tmp_path, model)
    try:
        legacy = {
            "files": {
                "project/readme.md": "old-hash",
                "chat/notes.txt": "old-hash-2",
            }
        }
        ctx.state_file.parent.mkdir(parents=True, exist_ok=True)
        ctx.state_file.write_text(json.dumps(legacy), encoding="utf-8")
        result = run_pipeline(ctx, run_id="distill-migrate-1", yes=True, incremental=True)
        assert result.status is RunStatus.COMPLETED
        assert len(ctx.knowledge_store.list_sources()) == 2
        migrated = DistillState.model_validate_json(ctx.state_file.read_text(encoding="utf-8"))
        assert all(entry.source_id for entry in migrated.files.values())
    finally:
        ctx.close()


def test_distilled_metadata_does_not_expose_input_path(tmp_path: Path) -> None:
    model = ScriptedDistillModel([
        _atoms_response("我会保护项目输入路径。"),
        _atoms_response("我会保护聊天输入路径并保持来源可追溯。"),
    ])
    ctx = _scripted_context(tmp_path, model)
    try:
        result = run_pipeline(ctx, run_id="distill-metadata-1", yes=True)
        assert result.status is RunStatus.COMPLETED
        sources = ctx.knowledge_store.list_sources()
        assert sources
        for source in sources:
            assert "project/" not in (source.public_summary or "")
            assert "chat/" not in (source.public_summary or "")
            assert "readme.md" not in source.title
            assert source.project == "Approved Knowledge"
    finally:
        ctx.close()
