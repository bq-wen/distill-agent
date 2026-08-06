"""Distillation graph assembly: the supervised write pipeline.

One guard chain serves both write gates: the audit artifact writer (MEDIUM risk)
and the knowledge-base indexer (HIGH risk). Under ``ExecutionMode.SUPERVISED``
both requests pause at ``distillation_approval_end``; the executor persists a
checkpoint, and ``resume(run_id, approved)`` continues at the tool executor or
at the shared deny feedback node.
"""

from personal_agent.distillation.nodes import (
    AbortEndNode,
    AuditGateNode,
    CleanerNode,
    CompletionNode,
    ContentRouter,
    ContinueRouter,
    DistillationApprovalEnd,
    ExtractorNode,
    IndexerNode,
    SourceLoaderNode,
    StructurerNode,
)
from personal_agent.distillation.tools import IndexDocumentsTool, WriteAuditArtifactTool
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.wengraph_runtime import (
    AgentFinishNode,
    Capability,
    CapabilityPolicy,
    ChatModel,
    DenyEndNode,
    Edge,
    ExecutionMode,
    Graph,
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


def build_distillation_graph(
    *,
    artifact_store,
    chat_model: ChatModel,
    knowledge: PersonalKnowledgeService,
    audit_dir: str,
    input_dir: str,
    run_id: str,
    extraction_prompt: str | None = None,
    only_files: set[str] | None = None,
    deleted_source_ids: set[str] | None = None,
) -> tuple[Graph, list]:
    """Assemble the supervised distillation pipeline and return the guarded tools."""

    registry = ToolRegistry()
    audit_tool = WriteAuditArtifactTool(audit_dir)
    index_tool = IndexDocumentsTool(knowledge)
    for tool in (audit_tool, index_tool):
        registry.register(tool)

    loader = SourceLoaderNode(input_dir, artifact_store, only_files=only_files)
    cleaner = CleanerNode(artifact_store)
    extractor = ExtractorNode(chat_model, artifact_store, prompt=extraction_prompt)
    structurer = StructurerNode(artifact_store)
    content_router = ContentRouter(artifact_store, deleted_source_ids=deleted_source_ids)
    audit_gate = AuditGateNode(artifact_store, run_id=run_id, deleted_source_ids=deleted_source_ids)
    indexer = IndexerNode(artifact_store, deleted_source_ids=deleted_source_ids)

    guard = ToolGuard(CapabilityPolicy(), RiskPolicy(ExecutionMode.SUPERVISED))
    guard.capability_policy.register_tool(
        ToolSpec(name=audit_tool.name, required_capabilities={Capability.WRITE_SANDBOX}, risk_level=RiskLevel.MEDIUM)
    )
    guard.capability_policy.register_tool(
        ToolSpec(name=index_tool.name, required_capabilities={Capability.DATABASE_WRITE}, risk_level=RiskLevel.HIGH)
    )
    guard.capability_policy.grant(audit_gate.name, {Capability.WRITE_SANDBOX})
    guard.capability_policy.grant(indexer.name, {Capability.DATABASE_WRITE})
    guard_node = ToolGuardNode(guard)
    policy_router = PolicyRouterNode()
    # ApprovalEndNode resumes at this exact node name after human approval.
    tool_node = ToolNode(registry, node_name="execute_tool")
    approval_end = DistillationApprovalEnd()
    deny_end = DenyEndNode()
    continue_router = ContinueRouter()
    abort_end = AbortEndNode()
    completion = CompletionNode()
    finish = AgentFinishNode()

    graph = Graph()
    for node in (
        loader,
        cleaner,
        extractor,
        structurer,
        content_router,
        audit_gate,
        indexer,
        guard_node,
        policy_router,
        tool_node,
        approval_end,
        deny_end,
        continue_router,
        abort_end,
        completion,
        finish,
    ):
        graph.add_node(node)
    graph.add_edge(Edge(loader, cleaner))
    graph.add_edge(Edge(cleaner, extractor))
    graph.add_edge(Edge(extractor, structurer))
    graph.add_edge(Edge(structurer, content_router))
    graph.add_edge(Edge(content_router, audit_gate, condition="gate"))
    graph.add_edge(Edge(content_router, finish, condition="skip"))
    graph.add_edge(Edge(audit_gate, guard_node))
    graph.add_edge(Edge(indexer, guard_node))
    graph.add_edge(Edge(guard_node, policy_router))
    graph.add_edge(Edge(policy_router, tool_node, condition=PolicyDecision.ALLOW.value))
    graph.add_edge(Edge(policy_router, approval_end, condition=PolicyDecision.REQUIRE_APPROVAL.value))
    graph.add_edge(Edge(policy_router, deny_end, condition=PolicyDecision.DENY.value))
    graph.add_edge(Edge(tool_node, continue_router))
    graph.add_edge(Edge(deny_end, continue_router))
    graph.add_edge(Edge(continue_router, indexer, condition="index"))
    graph.add_edge(Edge(continue_router, abort_end, condition="abort"))
    graph.add_edge(Edge(continue_router, completion, condition="finish"))
    graph.add_edge(Edge(completion, finish))
    graph.add_edge(Edge(abort_end, finish))

    _apply_policies(
        graph,
        loader=loader,
        cleaner=cleaner,
        extractor=extractor,
        structurer=structurer,
        content_router=content_router,
        audit_gate=audit_gate,
        indexer=indexer,
        guard_node=guard_node,
        policy_router=policy_router,
        tool_node=tool_node,
        approval_end=approval_end,
        deny_end=deny_end,
        continue_router=continue_router,
        abort_end=abort_end,
        completion=completion,
        finish=finish,
    )
    graph.set_start_node(loader)
    graph.set_end_node(finish)
    return graph, [audit_tool, index_tool]


def _apply_policies(graph: Graph, **nodes: object) -> None:
    """Install per-node read/write StateField policies for the pipeline."""

    graph.set_read_policy(nodes["loader"], {StateField.MESSAGE})
    graph.set_write_policy(nodes["loader"], {StateField.MESSAGE, StateField.ARTIFACTS})
    for name in ("cleaner", "extractor", "structurer"):
        graph.set_read_policy(nodes[name], {StateField.ARTIFACTS})
        graph.set_write_policy(nodes[name], {StateField.ARTIFACTS, StateField.MESSAGE})
    graph.set_read_policy(nodes["content_router"], {StateField.ARTIFACTS})
    graph.set_write_policy(nodes["content_router"], set())
    graph.set_read_policy(nodes["audit_gate"], {StateField.ARTIFACTS, StateField.MESSAGE})
    graph.set_write_policy(
        nodes["audit_gate"],
        {
            StateField.ARTIFACTS,
            StateField.MESSAGE,
            StateField.PENDING_TOOL_REQUEST,
            StateField.PENDING_TOOL_REQUESTS,
            StateField.TOOL_REQUESTER,
        },
    )
    graph.set_read_policy(nodes["indexer"], {StateField.ARTIFACTS, StateField.LAST_TOOL_RESULT, StateField.MESSAGE})
    graph.set_write_policy(
        nodes["indexer"],
        {
            StateField.ARTIFACTS,
            StateField.MESSAGE,
            StateField.PENDING_TOOL_REQUEST,
            StateField.PENDING_TOOL_REQUESTS,
            StateField.TOOL_REQUESTER,
        },
    )
    graph.set_read_policy(
        nodes["guard_node"],
        {StateField.PENDING_TOOL_REQUEST, StateField.PENDING_TOOL_REQUESTS, StateField.TOOL_REQUESTER},
    )
    graph.set_write_policy(nodes["guard_node"], {StateField.POLICY_DECISION})
    graph.set_read_policy(nodes["policy_router"], {StateField.POLICY_DECISION})
    graph.set_write_policy(nodes["policy_router"], set())
    graph.set_read_policy(nodes["tool_node"], {StateField.PENDING_TOOL_REQUEST, StateField.PENDING_TOOL_REQUESTS})
    graph.set_write_policy(nodes["tool_node"], {StateField.LAST_TOOL_RESULT, StateField.TOOL_HISTORY})
    graph.set_read_policy(nodes["approval_end"], set())
    graph.set_write_policy(nodes["approval_end"], {StateField.MESSAGE})
    graph.set_read_policy(
        nodes["deny_end"],
        {StateField.PENDING_TOOL_REQUEST, StateField.PENDING_TOOL_REQUESTS, StateField.POLICY_DECISION},
    )
    graph.set_write_policy(
        nodes["deny_end"], {StateField.LAST_TOOL_RESULT, StateField.TOOL_HISTORY, StateField.MESSAGE}
    )
    graph.set_read_policy(nodes["continue_router"], {StateField.LAST_TOOL_RESULT})
    graph.set_write_policy(nodes["continue_router"], set())
    graph.set_read_policy(nodes["abort_end"], {StateField.LAST_TOOL_RESULT})
    graph.set_write_policy(nodes["abort_end"], {StateField.MESSAGE})
    graph.set_read_policy(nodes["completion"], {StateField.LAST_TOOL_RESULT})
    graph.set_write_policy(nodes["completion"], {StateField.MESSAGE})
    graph.set_read_policy(nodes["finish"], set())
    graph.set_write_policy(nodes["finish"], set())
