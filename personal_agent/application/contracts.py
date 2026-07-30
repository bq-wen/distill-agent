"""Application-level answer contracts independent of HTTP and WenGraph state."""

from pydantic import BaseModel, Field

from personal_agent.contracts import PublicCitation


class AgentAnswer(BaseModel):
    """A completed personal-agent answer with only visitor-safe citations."""

    text: str = Field(min_length=1)
    citations: list[PublicCitation] = Field(default_factory=list)
