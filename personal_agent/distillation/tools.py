"""Guarded write tools for the distillation pipeline.

Both tools are ``IDEMPOTENT_WRITE`` and carry MEDIUM/HIGH risk so that under
``ExecutionMode.SUPERVISED`` the ToolGuard pauses the graph for human approval
before anything touches disk.
"""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from personal_agent.distillation.contracts import DistillDocument
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.wengraph_runtime import Tool, ToolEffect


class WriteAuditArguments(BaseModel):
    run_id: str = Field(min_length=1)
    audit_json: str = Field(min_length=1)


class WriteAuditArtifactTool(Tool):
    """Persist the reviewable audit artifact (atoms + documents) for traceability."""

    name = "write_audit_artifact"
    description = "把本次蒸馏的原子与文档清单写入审计目录，供追溯与审核。"
    args_model = WriteAuditArguments
    effect = ToolEffect.IDEMPOTENT_WRITE

    def __init__(self, audit_dir: str | Path) -> None:
        self.audit_dir = Path(audit_dir)

    async def execute(self, args: WriteAuditArguments) -> str:
        path = self.audit_dir / f"{args.run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(args.audit_json, encoding="utf-8")
        return f"已写入审计产物 {path.name}"


class IndexDocumentsArguments(BaseModel):
    documents_json: str = Field(min_length=1)


class IndexDocumentsTool(Tool):
    """Index approved distilled documents into the shared knowledge base."""

    name = "index_documents"
    description = "把审核通过的蒸馏文档索引进知识库（embedding + 关键词检索）。"
    args_model = IndexDocumentsArguments
    effect = ToolEffect.IDEMPOTENT_WRITE

    def __init__(self, knowledge: PersonalKnowledgeService) -> None:
        self.knowledge = knowledge

    async def execute(self, args: IndexDocumentsArguments) -> str:
        documents = [
            DistillDocument.model_validate(item)
            for item in json.loads(args.documents_json)
        ]
        total_chunks = 0
        indexed = 0
        for document in documents:
            indexed += 1
            total_chunks += self.knowledge.index_markdown_text(document.content, path=document.source_id)
        return f"已索引 {indexed} 篇文档（{total_chunks} 个分块）"
