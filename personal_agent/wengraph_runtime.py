"""The only import boundary between this application and the fixed WenGraph submodule."""

import sys
from pathlib import Path


WENGRAPH_ROOT = Path(__file__).parents[1] / "vendor" / "wengraph"
if not WENGRAPH_ROOT.is_dir():
    raise RuntimeError("未找到 vendor/wengraph；请初始化 Git Submodule")
if str(WENGRAPH_ROOT) not in sys.path:
    sys.path.insert(0, str(WENGRAPH_ROOT))

from wengraph import (  # noqa: E402
    AgentFinishNode,
    AgentRouterNode,
    ApprovalEndNode,
    Capability,
    CapabilityPolicy,
    Checkpoint,
    DenyEndNode,
    Edge,
    ExecutionMode,
    Graph,
    GraphExecutor,
    LLMNode,
    Node,
    PolicyDecision,
    PolicyRouterNode,
    RiskLevel,
    RiskPolicy,
    RouterNode,
    RunResult,
    RunStatus,
    State,
    StateField,
    StatePatch,
    StateView,
    Tool,
    ToolEffect,
    ToolGuard,
    ToolGuardNode,
    ToolNode,
    ToolRegistry,
    ToolSpec,
)
from llm import ChatMessage, ChatModel, ModelResponse, OpenAIChatConfig, OpenAIChatModel
from memory import ContextBuilder, ConversationEvent, ConversationStore, InMemoryArtifactStore, InMemoryConversationStore
from storage.in_memory import InMemoryCheckpointStore, InMemoryRunStore, InMemoryToolExecutionStore
from storage.sqlite import (
    SQLiteArtifactStore,
    SQLiteCheckpointStore,
    SQLiteDatabase,
    SQLiteRunStore,
    SQLiteToolExecutionStore,
)
from tools import ToolRequest

__all__ = [
    "AgentFinishNode", "AgentRouterNode", "ApprovalEndNode", "Capability", "CapabilityPolicy",
    "ChatMessage", "ChatModel", "Checkpoint", "ContextBuilder", "ConversationEvent", "ConversationStore",
    "DenyEndNode", "Edge", "ExecutionMode", "Graph", "GraphExecutor", "InMemoryArtifactStore",
    "InMemoryCheckpointStore", "InMemoryConversationStore", "InMemoryRunStore", "InMemoryToolExecutionStore",
    "LLMNode", "ModelResponse", "Node", "OpenAIChatConfig", "OpenAIChatModel", "PolicyDecision",
    "PolicyRouterNode", "RiskLevel", "RiskPolicy", "RouterNode", "RunResult", "RunStatus", "SQLiteArtifactStore",
    "SQLiteCheckpointStore", "SQLiteDatabase", "SQLiteRunStore", "SQLiteToolExecutionStore",
    "State", "StateField", "StatePatch", "StateView", "Tool", "ToolEffect", "ToolGuard",
    "ToolGuardNode", "ToolNode", "ToolRegistry", "ToolRequest", "ToolSpec",
]
