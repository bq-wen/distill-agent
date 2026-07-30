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
    Capability,
    CapabilityPolicy,
    Edge,
    ExecutionMode,
    Graph,
    GraphExecutor,
    LLMNode,
    PolicyDecision,
    PolicyRouterNode,
    RiskLevel,
    RiskPolicy,
    RunStatus,
    State,
    StateField,
    Tool,
    ToolEffect,
    ToolGuard,
    ToolGuardNode,
    ToolNode,
    ToolRegistry,
    ToolSpec,
)
from llm import ChatModel
from memory import ContextBuilder, InMemoryArtifactStore, InMemoryConversationStore
from tools import ToolRequest

__all__ = [
    "AgentFinishNode", "AgentRouterNode", "Capability", "CapabilityPolicy", "ChatModel",
    "ContextBuilder", "Edge", "ExecutionMode", "Graph", "GraphExecutor",
    "InMemoryArtifactStore", "InMemoryConversationStore", "LLMNode", "PolicyDecision",
    "PolicyRouterNode", "RiskLevel", "RiskPolicy", "RunStatus", "State", "StateField", "Tool",
    "ToolEffect", "ToolGuard", "ToolGuardNode", "ToolNode", "ToolRegistry", "ToolRequest",
    "ToolSpec",
]
