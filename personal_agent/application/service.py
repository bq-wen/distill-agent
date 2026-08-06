"""Single-turn application service used by the future queue worker."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from personal_agent.application.contracts import AgentAnswer
from personal_agent.application.graph import build_personal_graph
from personal_agent.application.profile import build_persona_prompt, load_profile
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.wengraph_runtime import (
    ChatModel,
    ConversationEvent,
    ConversationStore,
    GraphExecutor,
    RunStatus,
    State,
)


class PersonalAgentService:
    """Runs forced initial evidence retrieval before a guarded WenGraph ReAct turn."""

    def __init__(
        self,
        knowledge: PersonalKnowledgeService,
        chat_model: ChatModel,
        *,
        minimum_semantic_score: float = 0.35,
        conversation_store: ConversationStore | None = None,
    ) -> None:
        if not -1 <= minimum_semantic_score <= 1:
            raise ValueError("minimum_semantic_score 必须在 -1 到 1 之间")
        self.knowledge = knowledge
        self.chat_model = chat_model
        self.minimum_semantic_score = minimum_semantic_score
        self.conversation_store = conversation_store

    async def answer(self, question: str, *, conversation_id: str) -> AgentAnswer:
        if not question.strip():
            raise ValueError("问题不能为空")
        initial_matches = self._initial_matches(question)
        evidence = self._render_initial_evidence(initial_matches)
        persona_prompt = build_persona_prompt(load_profile(self.knowledge.store))
        graph, tools = build_personal_graph(
            self.knowledge,
            self.chat_model,
            conversation_store=self.conversation_store,
            persona_prompt=persona_prompt,
        )
        run_id = f"personal-{uuid4()}"
        result = await GraphExecutor(
            graph,
            State(
                message=f"用户问题：{question.strip()}\n\n首轮资料检索：\n{evidence}", conversation_id=conversation_id
            ),
            max_steps=24,
            max_tool_calls=4,
        ).run(run_id=run_id, timeout_seconds=90)
        if result.status is not RunStatus.COMPLETED:
            raise RuntimeError(f"个人 Agent 未完成: {result.status.value}; {result.error_message or '无错误说明'}")
        if result.state.message is None:
            raise RuntimeError("个人 Agent 未生成回答")
        citations_by_id = {match.source.source_id: match.public_citation for match in initial_matches}
        for tool in tools:
            citations_by_id.update({citation.source_id: citation for citation in tool.citations})
        answer = AgentAnswer(text=result.state.message, citations=list(citations_by_id.values()))
        if self.conversation_store is not None:
            now = datetime.now(UTC)
            self.conversation_store.append(
                ConversationEvent(
                    event_id=str(uuid4()),
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role="user",
                    content=question.strip(),
                    created_at=now,
                )
            )
            self.conversation_store.append(
                ConversationEvent(
                    event_id=str(uuid4()),
                    conversation_id=conversation_id,
                    run_id=run_id,
                    role="assistant",
                    content=answer.text,
                    created_at=now + timedelta(microseconds=1),
                )
            )
        return answer

    def _initial_matches(self, question: str):
        semantic = [
            match
            for match in self.knowledge.search_semantic(question, limit=3)
            if match.score >= self.minimum_semantic_score
        ]
        keywords = self.knowledge.search_keywords(question, limit=3)
        # 语义命中（已过阈值）优先于 FTS 精确命中：关键词匹配无法区分分数高低，
        # 若后写覆盖会丢掉语义排序信息。
        by_chunk = {match.chunk.chunk_id: match for match in [*keywords, *semantic]}
        return list(by_chunk.values())

    @staticmethod
    def _render_initial_evidence(matches) -> str:
        if not matches:
            return "未召回直接相关资料。个人事实必须说明资料未覆盖，不得猜测。"
        return "\n\n".join(
            f"[来源：{match.source.title} | {match.source.source_id}]\n{match.chunk.content}" for match in matches
        )
