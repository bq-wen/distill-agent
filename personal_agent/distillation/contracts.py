"""Data contracts for the knowledge distillation pipeline.

Every artifact produced by a distillation run stays traceable: atoms carry the
source file they were extracted from, and the audit artifact records exactly
what was written to the knowledge base after human approval.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

AtomKind = Literal["statement", "qa_pair", "fact"]
SourceType = Literal["resume", "project_readme", "git_history", "chat_export", "notes", "manual"]

# 共享的 Run ID 白名单：阻止路径穿越（audit 文件名由 run_id 拼接）与异常长度输入。
RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"


class KnowledgeAtom(BaseModel):
    """One distilled, first-person knowledge unit with full provenance."""

    atom_id: str = Field(min_length=1)
    content: str = Field(min_length=1, max_length=4_000)
    kind: AtomKind
    source_type: SourceType
    source_file: str = Field(min_length=1)
    extracted_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    review_note: str | None = None

    @model_validator(mode="after")
    def validate_qa_shape(self) -> "KnowledgeAtom":
        if self.kind == "qa_pair" and ("\n" not in self.content or len(self.content) < 10):
            raise ValueError("qa_pair 原子必须包含问题与回答两行")
        return self


class SourceFile(BaseModel):
    """One input file discovered by the loader, with artifact references."""

    index: int = Field(ge=0)
    path: str = Field(min_length=1)
    kind: Literal["markdown", "text", "json"]
    source_type: SourceType
    raw_artifact_id: str
    clean_artifact_id: str | None = None


class DistillDocument(BaseModel):
    """A ready-to-index Markdown document generated from atoms."""

    source_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    project: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    public_summary: str = Field(min_length=1)
    public_questions: list[str] = Field(default_factory=list)


class AuditArtifact(BaseModel):
    """Reviewable record of one distillation run; the only write that enters the audit dir."""

    run_id: str = Field(min_length=1, pattern=RUN_ID_PATTERN)
    created_at: datetime
    atoms: list[KnowledgeAtom] = Field(default_factory=list)
    documents: list[DistillDocument] = Field(default_factory=list)


class DistillState(BaseModel):
    """Incremental-distillation bookkeeping: content hashes of already indexed files."""

    files: dict[str, str] = Field(default_factory=dict)

    def is_unchanged(self, path: str, content_hash: str) -> bool:
        return self.files.get(path) == content_hash
