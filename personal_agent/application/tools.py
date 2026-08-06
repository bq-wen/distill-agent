"""Read-only WenGraph tools over the personal knowledge service."""

from pydantic import BaseModel, Field

from personal_agent.contracts import PublicCitation
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.wengraph_runtime import Tool, ToolEffect


class SearchArguments(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class _PersonalSearchTool(Tool):
    """Shared result shaping and citation collection for safe personal retrieval."""

    args_model = SearchArguments
    effect = ToolEffect.READ_ONLY
    max_attempts = 2
    retry_backoff_seconds = 0.1

    def __init__(self, knowledge: PersonalKnowledgeService) -> None:
        self.knowledge = knowledge
        self.citations: list[PublicCitation] = []

    def _render_matches(self, matches) -> str:
        if not matches:
            return "未找到与该问题直接相关的个人资料。不要据此编造个人经历。"
        citations_by_id = {citation.source_id: citation for citation in self.citations}
        for match in matches:
            citations_by_id[match.source.source_id] = match.public_citation
        self.citations = list(citations_by_id.values())
        sections = []
        for match in matches:
            heading = f"\n章节：{match.chunk.heading}" if match.chunk.heading else ""
            sections.append(f"[来源：{match.source.title} | {match.source.source_id}]{heading}\n{match.chunk.content}")
        return "\n\n".join(sections)


class SearchPersonalSemanticTool(_PersonalSearchTool):
    name = "search_personal_semantic"
    description = "按语义检索个人授权项目资料。用于补充项目经历、架构、贡献和技术决策的证据。"

    async def execute(self, args: SearchArguments) -> str:
        return self._render_matches(self.knowledge.search_semantic(args.query, limit=args.limit))


class SearchPersonalKeywordsTool(_PersonalSearchTool):
    name = "search_personal_keywords"
    description = "按关键词精确检索个人授权资料。适合项目名、框架名、缩写、接口名和技术名词。"

    async def execute(self, args: SearchArguments) -> str:
        return self._render_matches(self.knowledge.search_keywords(args.query, limit=args.limit))
