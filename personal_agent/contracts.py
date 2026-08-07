"""Typed application contracts shared by knowledge, service, and API layers."""

from enum import StrEnum

from pydantic import AnyHttpUrl, BaseModel, Field, model_validator


class KnowledgeVisibility(StrEnum):
    """Visibility of the source document itself, not its public citation."""

    PRIVATE = "private"
    PUBLIC = "public"


class SourceMetadata(BaseModel):
    """A stable source record with the only metadata safe to expose to visitors."""

    source_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    project: str = Field(min_length=1)
    title: str = Field(min_length=1)
    visibility: KnowledgeVisibility
    public_summary: str | None = None
    public_url: AnyHttpUrl | None = None
    public_questions: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="访客可见的推荐问题，驱动前端主题区；每条不超过 120 字符。",
    )
    topics: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="主题标签，用于 /api/topics 分组展示。",
    )

    @model_validator(mode="after")
    def validate_public_metadata(self) -> "SourceMetadata":
        if self.visibility is KnowledgeVisibility.PUBLIC and not self.public_summary:
            raise ValueError("公开资料必须提供 public_summary")
        return self


class PublicCitation(BaseModel):
    """Citation shape returned to web visitors; raw document data is excluded."""

    source_id: str
    project: str
    title: str
    summary: str
    url: AnyHttpUrl | None = None

    @classmethod
    def from_source(cls, source: SourceMetadata) -> "PublicCitation":
        return cls(
            source_id=source.source_id,
            project=source.project,
            title=source.title,
            summary=source.public_summary or "该资料仅提供已审核的公开引用。",
            url=source.public_url,
        )


class HealthResponse(BaseModel):
    """Stable health response for local development and container probes."""

    status: str = "ok"
    service: str = "personal-agent"


class ProfileResponse(BaseModel):
    """Visitor-safe identity for the digital twin, driven by the profile document."""

    name: str = Field(min_length=1)
    monogram: str = Field(default="AI", min_length=1, max_length=4)
    role: str = Field(default="", max_length=120)
    github: str | None = None
    greeting: str = Field(default="", max_length=240)
    style: str = Field(default="", max_length=240)
    covered_topics: list[str] = Field(default_factory=list, max_length=24)


class TopicItem(BaseModel):
    """One knowledge source presented as a clickable topic in the frontend."""

    source_id: str
    title: str
    summary: str
    url: AnyHttpUrl | None = None
    questions: list[str] = Field(default_factory=list)


class TopicGroup(BaseModel):
    """Knowledge sources grouped by project for the frontend topics panel."""

    project: str
    topics: list[TopicItem] = Field(default_factory=list)
