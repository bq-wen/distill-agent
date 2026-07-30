import asyncio
from pathlib import Path

from personal_agent.application.service import PersonalAgentService
from personal_agent.knowledge.documents import parse_markdown_document
from personal_agent.knowledge.embedding import HashEmbeddingProvider
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore
from personal_agent.wengraph_runtime import ToolRequest
from llm import ChatModel, ModelResponse


class ScriptedChatModel(ChatModel):
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.messages = []

    async def complete(self, messages, tools) -> ModelResponse:
        self.messages.append((messages, tools))
        return self.responses.pop(0)


def _knowledge_service(tmp_path: Path) -> PersonalKnowledgeService:
    document = tmp_path / "wengraph.md"
    document.write_text(
        """---
source_id: wengraph-overview
project: WenGraph
title: WenGraph 架构说明
visibility: private
public_summary: 自研 Agent 图运行时。
public_url: https://github.com/bq-wen/wengraph
---
# WenGraph

WenGraph 使用 StatePatch、ToolGuard 和可恢复执行来治理 Agent 工具调用。
""",
        encoding="utf-8",
    )
    store = KnowledgeStore(tmp_path / "knowledge.db")
    service = PersonalKnowledgeService(store, HashEmbeddingProvider())
    service.index_document(parse_markdown_document(document))
    return service


def test_personal_agent_forces_initial_evidence_and_allows_guarded_second_search(tmp_path: Path) -> None:
    model = ScriptedChatModel(
        [
            ModelResponse(
                tool_request=ToolRequest(
                    call_id="lookup-1",
                    tool_name="search_personal_keywords",
                    arguments={"query": "ToolGuard", "limit": 3},
                )
            ),
            ModelResponse(text="我在 WenGraph 中用 ToolGuard 治理只读工具调用。"),
        ]
    )
    answer = asyncio.run(
        PersonalAgentService(_knowledge_service(tmp_path), model).answer(
            "你如何控制 Agent 工具安全？", conversation_id="tab-1"
        )
    )

    assert "首轮资料检索" in model.messages[0][0][-1].content
    assert model.messages[1][0][-1].role == "tool"
    assert answer.text == "我在 WenGraph 中用 ToolGuard 治理只读工具调用。"
    assert [citation.source_id for citation in answer.citations] == ["wengraph-overview"]


def test_personal_agent_tells_model_when_personal_evidence_is_missing(tmp_path: Path) -> None:
    model = ScriptedChatModel([ModelResponse(text="这是一般技术说明，不代表我的个人经历。")])

    asyncio.run(
        PersonalAgentService(
            _knowledge_service(tmp_path), model, minimum_semantic_score=0.95
        ).answer(
            "你上一家公司为什么离职？", conversation_id="tab-1"
        )
    )

    assert "个人事实必须说明资料未覆盖" in model.messages[0][0][-1].content
