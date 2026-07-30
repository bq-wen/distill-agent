"""Application-level answer contracts independent of HTTP and WenGraph state."""

from pydantic import BaseModel, Field

from personal_agent.contracts import PublicCitation


class AgentAnswer(BaseModel):
    """A completed personal-agent answer with only visitor-safe citations."""

    text: str = Field(min_length=1)
    citations: list[PublicCitation] = Field(default_factory=list)


class SubmitMessage(BaseModel):
    """Validated HTTP input before it reaches the scheduler and persistence layer."""

    question: str = Field(min_length=1, max_length=2_000)


class RunResponse(BaseModel):
    """Visitor-safe projection of a persisted application Run."""

    run_id: str
    status: str
    answer: AgentAnswer | None = None
    error_message: str | None = None
