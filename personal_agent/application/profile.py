"""Profile-driven identity: persona prompt building and topics assembly.

The profile document (``knowledge/profile.md`` with ``profile: true``) is the
single source of truth for the digital twin's identity. Without it the service
falls back to a neutral default persona so the demo never breaks.
"""

from collections import defaultdict

from pydantic import BaseModel, Field

from personal_agent.contracts import SourceMetadata, TopicGroup, TopicItem
from personal_agent.knowledge.store import KnowledgeStore

PROFILE_SOURCE_ID = "profile"


class ProfileData(BaseModel):
    """Identity fields extracted from the profile document front matter."""

    name: str = Field(min_length=1)
    monogram: str = "AI"
    role: str = ""
    github: str | None = None
    greeting: str = ""
    style: str = ""
    covered_topics: list[str] = Field(default_factory=list)


DEFAULT_PERSONA_PROMPT = """你是基于本人授权资料构建的 AI 数字分身，不是本人实时在线。
你可以用第一人称介绍我的项目和工程思考，但所有个人经历、贡献、项目事实和个人观点必须以当前
消息或检索工具返回的授权资料为依据，并在回答末尾标明使用过的来源标题。资料不足时明确说明
“我的当前资料没有覆盖这部分，建议面试时向本人确认”，绝不能补全或猜测。

一般技术问题可以用通用知识回答，但必须明确它不等同于我的个人经历。对于私人、敏感、未公开或
保密信息，说明该信息不在公开资料范围内。检索工具只读且只能用于授权个人资料。"""

RETRIEVAL_CONVERGENCE_PROMPT = """

检索收敛规则：当前用户消息已经包含一次首轮资料检索。先判断这些证据是否足以回答；足够时直接给出
最终回答，不要再次调用工具。只有缺少回答所必需的具体证据时才调用一个最合适的检索工具，并在工具
返回后基于已有全部证据收尾。不要用同义改写反复搜索，也不要重复查询已经返回过的资料。"""


def build_persona_prompt(profile: ProfileData | None) -> str:
    """Build the system prompt for one serving graph; falls back when no profile is indexed."""

    if profile is None:
        return DEFAULT_PERSONA_PROMPT + RETRIEVAL_CONVERGENCE_PROMPT

    identity_lines = [f"你是{profile.name}的 AI 数字分身，不是本人实时在线。"]
    if profile.role:
        identity_lines.append(f"本人的方向：{profile.role}。")
    if profile.covered_topics:
        identity_lines.append(f"本人的知识覆盖主题：{'、'.join(profile.covered_topics)}。")
    if profile.style:
        identity_lines.append(f"回答风格遵循本人偏好：{profile.style}。")
    return "\n".join(identity_lines) + """

所有个人经历、贡献、项目事实和个人观点必须以当前消息或检索工具返回的授权资料为依据，并在回答
末尾标明使用过的来源标题。资料不足时明确说明“我的当前资料没有覆盖这部分，建议面试时向本人确认”，
绝不能补全或猜测。一般技术问题可以用通用知识回答，但必须明确它不等同于本人的个人经历。对于私人、
敏感、未公开或保密信息，说明该信息不在公开资料范围内。检索工具只读且只能用于授权个人资料。""" + RETRIEVAL_CONVERGENCE_PROMPT


def load_profile(store: KnowledgeStore) -> ProfileData | None:
    """Load identity fields from the indexed profile document, or None."""

    raw = store.profile_data()
    if raw is None:
        return None
    return ProfileData.model_validate({key: value for key, value in raw.items() if value is not None})


def list_topic_groups(sources: list[SourceMetadata]) -> list[TopicGroup]:
    """Group sources by project, keeping curated questions for each source."""

    grouped: dict[str, list[TopicItem]] = defaultdict(list)
    for source in sources:
        grouped[source.project].append(
            TopicItem(
                source_id=source.source_id,
                title=source.title,
                summary=source.public_summary or "该资料仅提供已审核的公开引用。",
                url=source.public_url,
                questions=source.public_questions,
            )
        )
    return [
        TopicGroup(project=project, topics=topics)
        for project, topics in sorted(grouped.items())
    ]
