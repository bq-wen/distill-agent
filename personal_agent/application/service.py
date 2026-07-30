"""Single-turn application service used by the future queue worker."""

from uuid import uuid4

from personal_agent.application.contracts import AgentAnswer
from personal_agent.application.graph import build_personal_graph
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.wengraph_runtime import ChatModel, GraphExecutor, RunStatus, State


class PersonalAgentService:
    """Runs forced initial evidence retrieval before a guarded WenGraph ReAct turn."""

    def __init__(
        self,
        knowledge: PersonalKnowledgeService,
        chat_model: ChatModel,
        *,
        minimum_semantic_score: float = 0.35,
    ) -> None:
        if not -1 <= minimum_semantic_score <= 1:
            raise ValueError("minimum_semantic_score 必须在 -1 到 1 之间")
        self.knowledge = knowledge
        self.chat_model = chat_model
        self.minimum_semantic_score = minimum_semantic_score

    async def answer(self, question: str, *, conversation_id: str) -> AgentAnswer:
        if not question.strip():
            raise ValueError("问题不能为空")
        initial_matches = self._initial_matches(question)
        evidence = self._render_initial_evidence(initial_matches)
        graph, tools = build_personal_graph(self.knowledge, self.chat_model)
        result = await GraphExecutor(
            graph,
            State(message=f"用户问题：{question.strip()}\n\n首轮资料检索：\n{evidence}", conversation_id=conversation_id),
            max_steps=12,
            max_tool_calls=4,
        ).run(run_id=f"personal-{uuid4()}", timeout_seconds=90)
        if result.status is not RunStatus.COMPLETED:
            raise RuntimeError(f"个人 Agent 未完成: {result.status.value}; {result.error_message or '无错误说明'}")
        if result.state.message is None:
            raise RuntimeError("个人 Agent 未生成回答")
        citations_by_id = {match.source.source_id: match.public_citation for match in initial_matches}
        for tool in tools:
            citations_by_id.update({citation.source_id: citation for citation in tool.citations})
        return AgentAnswer(text=result.state.message, citations=list(citations_by_id.values()))

    def _initial_matches(self, question: str):
        semantic = [
            match
            for match in self.knowledge.search_semantic(question, limit=3)
            if match.score >= self.minimum_semantic_score
        ]
        keywords = self.knowledge.search_keywords(question, limit=3)
        by_chunk = {match.chunk.chunk_id: match for match in [*semantic, *keywords]}
        return list(by_chunk.values())

    @staticmethod
    def _render_initial_evidence(matches) -> str:
        if not matches:
            return "未召回直接相关资料。个人事实必须说明资料未覆盖，不得猜测。"
        return "\n\n".join(
            f"[来源：{match.source.title} | {match.source.source_id}]\n{match.chunk.content}"
            for match in matches
        )
