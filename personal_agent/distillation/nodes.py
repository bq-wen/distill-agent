"""Deterministic distillation pipeline nodes.

The pipeline is a linear WenGraph: SourceLoader → Cleaner → Extractor(LLM) →
Structurer → AuditGate → Indexer. The two write steps emit guarded tool
requests; under ``ExecutionMode.SUPERVISED`` the runtime pauses for human
approval before they execute, so unapproved atoms never reach the knowledge
base. Pipeline payloads travel through the ArtifactStore as refs in ``state``.
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from personal_agent.distillation.contracts import (
    AuditArtifact,
    DistillDocument,
    KnowledgeAtom,
    SourceFile,
)
from personal_agent.wengraph_runtime import (
    ApprovalEndNode,
    ChatMessage,
    ChatModel,
    Node,
    RouterNode,
    StatePatch,
    StateView,
    ToolRequest,
)

EXTRACTION_PROMPT = """你是个人知识蒸馏器。把用户提供的原始材料提炼成结构化知识原子，用于构建某人的 AI 数字分身。

规则：
1. 只提炼与人物画像、技术经历、工程决策、项目细节、观点相关的信息。
2. 每条原子必须是第一人称陈述（“我做了/我选择/我认为”）、面试问答对（qa_pair）或客观事实（fact）。
3. qa_pair 的 content 必须包含问题与回答两行，格式：问题：xxx\\n回答：yyy。优先把可复述的经历提炼成面试问答。
4. 丢弃：情绪化闲聊、问候、表情、无关内容、明显涉他人隐私的信息。
5. 每条原子填写 kind（statement/qa_pair/fact）、confidence（0-1，依据信息明确度）、source_type（resume/project_readme/git_history/chat_export/notes/manual）。
6. 只输出 JSON，不要解释，格式：{"atoms": [{"content": "...", "kind": "statement", "confidence": 0.9, "source_type": "notes"}, ...]}"""


def _manifest_ref(state: StateView, kind: str) -> str | None:
    refs = state.artifacts or {}
    for artifact_id, ref in refs.items():
        if ref.kind == kind:
            return artifact_id
    return None


def _load_manifest(artifact_store, state: StateView, kind: str) -> list[SourceFile]:
    ref_id = _manifest_ref(state, kind)
    if ref_id is None:
        raise ValueError(f"状态中缺少 {kind} 清单")
    return [SourceFile.model_validate(entry) for entry in json.loads(artifact_store.get_text(ref_id))]


def _put_json(artifact_store, kind: str, value, *, summary: str):
    return artifact_store.put_text(kind, json.dumps(value, ensure_ascii=False, default=str), summary=summary)


def _clean_text(raw: str) -> str:
    text = raw.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        # 丢弃常见聊天元信息行，保留真正的对话内容。
        if re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?\s*[-—]?.*", stripped) and len(stripped) < 40:
            continue
        lines.append(stripped)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _source_type_for(path: Path, suffix: str) -> str:
    lowered = path.name.lower()
    if "resume" in lowered or "简历" in lowered:
        return "resume"
    if "readme" in lowered:
        return "project_readme"
    if suffix == ".json":
        return "chat_export"
    return "notes"


def source_id_for(source_file: str) -> str:
    """Stable opaque ID for a distilled input; visitor metadata must not reveal its path."""

    digest = hashlib.sha256(source_file.encode("utf-8")).hexdigest()[:16]
    return f"distilled-{digest}"


def legacy_source_id_for(source_file: str) -> str:
    """ID used before opaque source IDs, retained only for incremental cleanup."""

    stem = Path(source_file).stem.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "source"
    digest = hashlib.sha1(source_file.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}"


class SourceLoaderNode(Node):
    name = "source_loader_node"

    def __init__(
        self,
        input_dir: str | Path,
        artifact_store,
        only_files: set[str] | None = None,
    ) -> None:
        self.input_dir = Path(input_dir)
        self.artifact_store = artifact_store
        # 增量模式：只载入指定文件（相对路径）；None 表示全量。
        self.only_files = only_files

    async def execute(self, state: StateView) -> StatePatch:
        if not self.input_dir.is_dir():
            raise ValueError(f"输入目录不存在: {self.input_dir}")
        files = sorted(
            path
            for path in self.input_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".md", ".txt", ".json"}
            and (self.only_files is None or str(path.relative_to(self.input_dir)) in self.only_files)
        )
        if not files and self.only_files is None:
            raise ValueError("增量模式下没有内容变化的文件，无需蒸馏")

        entries: list[SourceFile] = []
        refs = {}
        for index, path in enumerate(files):
            raw = path.read_text(encoding="utf-8", errors="replace")
            suffix = path.suffix.lower()
            ref = self.artifact_store.put_text("raw", raw, summary=path.name)
            refs[ref.artifact_id] = ref
            entries.append(
                SourceFile(
                    index=index,
                    path=str(path.relative_to(self.input_dir)),
                    kind="markdown" if suffix == ".md" else "text" if suffix == ".txt" else "json",
                    source_type=_source_type_for(path, suffix),  # type: ignore[arg-type]
                    raw_artifact_id=ref.artifact_id,
                )
            )
        manifest_ref = _put_json(self.artifact_store, "manifest", [entry.model_dump() for entry in entries], summary="源文件清单")
        refs[manifest_ref.artifact_id] = manifest_ref
        return StatePatch(artifacts=refs, message=f"载入 {len(files)} 个源文件")


class CleanerNode(Node):
    name = "cleaner_node"

    def __init__(self, artifact_store) -> None:
        self.artifact_store = artifact_store

    async def execute(self, state: StateView) -> StatePatch:
        entries = _load_manifest(self.artifact_store, state, "manifest")
        refs: dict[str, object] = {}
        cleaned: list[SourceFile] = []
        for entry in entries:
            raw = self.artifact_store.get_text(entry.raw_artifact_id)
            cleaned_text = _clean_text(raw)
            ref = self.artifact_store.put_text("clean", cleaned_text, summary=entry.path)
            refs[ref.artifact_id] = ref  # type: ignore[assignment]
            cleaned.append(entry.model_copy(update={"clean_artifact_id": ref.artifact_id}))
        manifest_ref = _put_json(self.artifact_store, "clean_manifest", [entry.model_dump() for entry in cleaned], summary="清洗后清单")
        refs[manifest_ref.artifact_id] = manifest_ref  # type: ignore[assignment]
        return StatePatch(artifacts=refs, message=f"清洗 {len(cleaned)} 个源文件")


class ExtractorNode(Node):
    name = "extractor_node"

    def __init__(self, chat_model: ChatModel, artifact_store, prompt: str | None = None) -> None:
        self.chat_model = chat_model
        self.artifact_store = artifact_store
        self.prompt = prompt or EXTRACTION_PROMPT

    async def execute(self, state: StateView) -> StatePatch:
        entries = _load_manifest(self.artifact_store, state, "clean_manifest")
        atoms: list[KnowledgeAtom] = []
        skipped = 0
        now = datetime.now(timezone.utc)
        for entry in entries:
            text = self.artifact_store.get_text(entry.clean_artifact_id)
            if not text.strip():
                continue
            response = await self.chat_model.complete(
                [
                    ChatMessage(role="system", content=self.prompt),
                    ChatMessage(role="user", content=f"原始材料：\n{text}"),
                ],
                tools=[],
            )
            parsed = self._parse_atoms(response.text, entry, now)
            atoms.extend(parsed["atoms"])
            skipped += parsed["skipped"]
        atoms_ref = _put_json(self.artifact_store, "atoms", [atom.model_dump(mode="json") for atom in atoms], summary=f"{len(atoms)} 个知识原子")
        return StatePatch(
            artifacts={atoms_ref.artifact_id: atoms_ref},
            message=f"提炼 {len(atoms)} 个知识原子（跳过 {skipped} 条）",
        )

    def _parse_atoms(self, raw_text: str | None, entry: SourceFile, now: datetime) -> dict:
        atoms: list[KnowledgeAtom] = []
        skipped = 0
        if not raw_text:
            return {"atoms": atoms, "skipped": 1}
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if not match:
                return {"atoms": atoms, "skipped": 1}
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                return {"atoms": atoms, "skipped": 1}
        candidates = payload.get("atoms") if isinstance(payload, dict) else payload
        if not isinstance(candidates, list):
            return {"atoms": atoms, "skipped": 1}
        for raw_atom in candidates:
            if not isinstance(raw_atom, dict) or not raw_atom.get("content"):
                skipped += 1
                continue
            try:
                atoms.append(
                    KnowledgeAtom(
                        atom_id=str(uuid4()),
                        content=str(raw_atom["content"]).strip(),
                        kind=raw_atom.get("kind", "statement"),
                        confidence=float(raw_atom.get("confidence", 0.5)),
                        source_type=raw_atom.get("source_type", entry.source_type),
                        source_file=entry.path,
                        extracted_at=now,
                    )
                )
            except (ValueError, TypeError):
                skipped += 1
        return {"atoms": atoms, "skipped": skipped}


class StructurerNode(Node):
    name = "structurer_node"

    def __init__(self, artifact_store) -> None:
        self.artifact_store = artifact_store

    async def execute(self, state: StateView) -> StatePatch:
        atoms_ref = _manifest_ref(state, "atoms")
        if atoms_ref is None:
            raise ValueError("缺少知识原子产物")
        atoms = [
            KnowledgeAtom.model_validate(item)
            for item in json.loads(self.artifact_store.get_text(atoms_ref))
        ]
        if not atoms:
            return StatePatch(message="没有可结构化的知识原子，流程结束")
        documents = self._build_documents(atoms)
        docs_ref = _put_json(self.artifact_store, "documents", [doc.model_dump() for doc in documents], summary=f"{len(documents)} 篇知识文档")
        return StatePatch(
            artifacts={docs_ref.artifact_id: docs_ref},
            message=f"生成 {len(documents)} 篇知识文档",
        )

    @staticmethod
    def _build_documents(atoms: list[KnowledgeAtom]) -> list[DistillDocument]:
        by_file: dict[str, list[KnowledgeAtom]] = {}
        for atom in atoms:
            by_file.setdefault(atom.source_file, []).append(atom)
        documents: list[DistillDocument] = []
        for source_file, file_atoms in sorted(by_file.items()):
            source_id = source_id_for(source_file)
            project = "Approved Knowledge"
            title = f"审核知识笔记 {source_id.removeprefix('distilled-')[:8]}"
            public_summary = f"经审核的个人知识资料，覆盖 {len(file_atoms)} 个知识点。"
            body: list[str] = []
            questions: list[str] = []
            for atom in file_atoms:
                heading = {"statement": "经历与观点", "qa_pair": "面试问答", "fact": "事实"}[atom.kind]
                body.append(f"## {heading}\n\n{atom.content}")
                if atom.kind == "qa_pair" and len(questions) < 12:
                    question = atom.content.split("\n", 1)[0].removeprefix("问题：").strip()
                    if question and question not in questions:
                        questions.append(question)
            front_matter = "\n".join(
                [
                    "---",
                    f"source_id: {source_id}",
                    f"project: {project}",
                    f"title: {title}",
                    "visibility: private",
                    f"public_summary: {public_summary}",
                    *([f"public_questions:" + "".join(f"\n  - {question}" for question in questions)] if questions else []),
                    "---",
                ]
            )
            documents.append(
                DistillDocument(
                    source_id=source_id,
                    project=project,
                    title=title,
                    content=f"{front_matter}\n\n# {title}\n\n" + "\n\n".join(body),
                    public_summary=public_summary,
                    public_questions=questions,
                )
            )
        return documents


class ContentRouter(RouterNode):
    """Route to the approval gates only when there is something to write."""

    name = "content_router"

    def __init__(self, artifact_store, *, deleted_source_ids: set[str] | None = None) -> None:
        self.artifact_store = artifact_store
        self.deleted_source_ids = deleted_source_ids or set()

    async def route(self, state: StateView) -> str:
        atoms_ref = _manifest_ref(state, "atoms")
        if atoms_ref is None:
            return "skip"
        try:
            payload = json.loads(self.artifact_store.get_text(atoms_ref))
        except (ValueError, KeyError):
            return "skip"
        return "gate" if payload or self.deleted_source_ids else "skip"


class AuditGateNode(Node):
    """Requests the guarded write of the audit artifact (MEDIUM risk → approval)."""

    name = "audit_gate_node"

    def __init__(self, artifact_store, run_id: str, *, deleted_source_ids: set[str] | None = None) -> None:
        self.artifact_store = artifact_store
        self.run_id = run_id
        self.deleted_source_ids = sorted(deleted_source_ids or set())

    async def execute(self, state: StateView) -> StatePatch:
        atoms = _load_artifact_list(self.artifact_store, state, "atoms", KnowledgeAtom)
        documents = _load_artifact_list(self.artifact_store, state, "documents", DistillDocument, required=False)
        payload = AuditArtifact(
            run_id=self.run_id,
            created_at=datetime.now(timezone.utc),
            atoms=atoms,
            documents=documents,
            deleted_source_ids=self.deleted_source_ids,
        )
        ref = self.artifact_store.put_text("audit", payload.model_dump_json(), summary="审计产物")
        request = ToolRequest(
            call_id=str(uuid4()),
            tool_name="write_audit_artifact",
            arguments={"run_id": self.run_id, "audit_json": payload.model_dump_json()},
        )
        return StatePatch(
            artifacts={ref.artifact_id: ref},
            pending_tool_requests=[request],
            tool_requester=self.name,
            message=(
                f"等待批准写入审计产物（{len(atoms)} 原子 / {len(documents)} 文档"
                f" / {len(self.deleted_source_ids)} 个删除）"
            ),
        )


class IndexerNode(Node):
    """Requests the guarded write of the knowledge base (HIGH risk → approval)."""

    name = "indexer_node"

    def __init__(self, artifact_store, *, deleted_source_ids: set[str] | None = None) -> None:
        self.artifact_store = artifact_store
        self.deleted_source_ids = sorted(deleted_source_ids or set())

    async def execute(self, state: StateView) -> StatePatch:
        documents = _load_artifact_list(self.artifact_store, state, "documents", DistillDocument, required=False)
        if not documents and not self.deleted_source_ids:
            return StatePatch(message="没有可索引的文档，流程结束")
        request = ToolRequest(
            call_id=str(uuid4()),
            tool_name="index_documents",
            arguments={
                "documents_json": json.dumps([doc.model_dump() for doc in documents], ensure_ascii=False),
                "deleted_source_ids": self.deleted_source_ids,
            },
        )
        return StatePatch(
            pending_tool_requests=[request],
            tool_requester=self.name,
            message=f"等待批准索引 {len(documents)} 篇文档到知识库",
        )


def _load_artifact_list(artifact_store, state: StateView, kind: str, model, *, required: bool = True) -> list:
    ref_id = _manifest_ref(state, kind)
    if ref_id is None:
        if required:
            raise ValueError(f"状态中缺少 {kind} 产物")
        return []
    return [model.model_validate(item) for item in json.loads(artifact_store.get_text(ref_id))]


class ContinueRouter(RouterNode):
    """After a guarded tool executes (or is denied), route to the next stage."""

    name = "continue_router"

    async def route(self, state: StateView) -> str:
        result = state.last_tool_result
        if result is None or not result.ok:
            return "abort"
        if result.tool_name == "write_audit_artifact":
            return "index"
        return "finish"


class AbortEndNode(Node):
    name = "abort_end_node"

    async def execute(self, state: StateView) -> StatePatch:
        reason = state.last_tool_result.error_message if state.last_tool_result else "审批未通过"
        return StatePatch(message=f"蒸馏流程中止：{reason or '审批拒绝或工具失败'}")


class CompletionNode(Node):
    """Write a meaningful completion message after the index tool succeeds."""

    name = "completion_node"

    async def execute(self, state: StateView) -> StatePatch:
        detail = state.last_tool_result.content if state.last_tool_result else ""
        return StatePatch(message=f"蒸馏完成：{detail}")


class DistillationApprovalEnd(ApprovalEndNode):
    """Approval pause that resumes on rejection into the shared deny feedback node."""

    name = "distillation_approval_end"
    reject_node_name = "deny_end"
