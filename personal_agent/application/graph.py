"""Safe ReAct graph assembly for the personal digital twin."""

from personal_agent.application.tools import SearchPersonalKeywordsTool, SearchPersonalSemanticTool
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.wengraph_runtime import (
    AgentFinishNode,
    AgentRouterNode,
    Capability,
    CapabilityPolicy,
    ChatModel,
    ContextBuilder,
    Edge,
    ExecutionMode,
    Graph,
    InMemoryArtifactStore,
    InMemoryConversationStore,
    LLMNode,
    PolicyDecision,
    PolicyRouterNode,
    RiskLevel,
    RiskPolicy,
    StateField,
    ToolGuard,
    ToolGuardNode,
    ToolNode,
    ToolRegistry,
    ToolSpec,
)


PERSONA_PROMPT = """你是基于本人授权资料构建的 AI 数字分身，不是本人实时在线。
你可以用第一人称介绍我的项目和工程思考，但所有个人经历、贡献、项目事实和个人观点必须以当前
消息或检索工具返回的授权资料为依据，并在回答末尾标明使用过的来源标题。资料不足时明确说明
“我的当前资料没有覆盖这部分，建议面试时向本人确认”，绝不能补全或猜测。

一般技术问题可以用通用知识回答，但必须明确它不等同于我的个人经历。对于私人、敏感、未公开或
保密信息，说明该信息不在公开资料范围内。检索工具只读且只能用于授权个人资料。"""


def build_personal_graph(
    knowledge: PersonalKnowledgeService,
    chat_model: ChatModel,
    *,
    conversation_store=None,
) -> tuple[Graph, list]:
    """Build the minimal guarded ReAct graph and return tools for citation collection."""

    registry = ToolRegistry()
    tools = [SearchPersonalSemanticTool(knowledge), SearchPersonalKeywordsTool(knowledge)]
    for tool in tools:
        registry.register(tool)

    llm = LLMNode(
        registry,
        chat_model,
        InMemoryArtifactStore(),
        ContextBuilder(conversation_store or InMemoryConversationStore(), system_prompt=PERSONA_PROMPT),
    )
    router = AgentRouterNode()
    guard = ToolGuard(CapabilityPolicy(), RiskPolicy(ExecutionMode.UNATTENDED))
    for tool in tools:
        guard.capability_policy.register_tool(
            ToolSpec(name=tool.name, required_capabilities={Capability.READ_CODE}, risk_level=RiskLevel.LOW)
        )
    guard.capability_policy.grant(llm.name, {Capability.READ_CODE})
    guard_node = ToolGuardNode(guard)
    policy_router = PolicyRouterNode()
    tool_node = ToolNode(registry)
    finish = AgentFinishNode()

    graph = Graph()
    for node in (llm, router, guard_node, policy_router, tool_node, finish):
        graph.add_node(node)
    graph.add_edge(Edge(llm, router))
    graph.add_edge(Edge(router, finish, condition="final"))
    graph.add_edge(Edge(router, guard_node, condition="tool_call"))
    graph.add_edge(Edge(guard_node, policy_router))
    graph.add_edge(Edge(policy_router, tool_node, condition=PolicyDecision.ALLOW.value))
    graph.add_edge(Edge(tool_node, llm))

    graph.set_read_policy(
        llm,
        {
            StateField.MESSAGE, StateField.CONVERSATION_ID, StateField.PENDING_TOOL_REQUEST,
            StateField.PENDING_TOOL_REQUESTS, StateField.LAST_TOOL_RESULT, StateField.TOOL_HISTORY,
        },
    )
    graph.set_write_policy(
        llm,
        {
            StateField.MESSAGE, StateField.NEXT_ACTION, StateField.PENDING_TOOL_REQUEST,
            StateField.PENDING_TOOL_REQUESTS, StateField.TOOL_REQUESTER,
        },
    )
    graph.set_read_policy(router, {StateField.NEXT_ACTION})
    graph.set_write_policy(router, set())
    graph.set_read_policy(
        guard_node, {StateField.PENDING_TOOL_REQUEST, StateField.PENDING_TOOL_REQUESTS, StateField.TOOL_REQUESTER}
    )
    graph.set_write_policy(guard_node, {StateField.POLICY_DECISION})
    graph.set_read_policy(policy_router, {StateField.POLICY_DECISION})
    graph.set_write_policy(policy_router, set())
    graph.set_read_policy(tool_node, {StateField.PENDING_TOOL_REQUEST, StateField.PENDING_TOOL_REQUESTS})
    graph.set_write_policy(tool_node, {StateField.LAST_TOOL_RESULT, StateField.TOOL_HISTORY})
    graph.set_read_policy(finish, set())
    graph.set_write_policy(finish, set())
    graph.set_start_node(llm)
    graph.set_end_node(finish)
    return graph, tools
