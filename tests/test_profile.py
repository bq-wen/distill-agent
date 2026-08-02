from pathlib import Path

from personal_agent.application.profile import (
    DEFAULT_PERSONA_PROMPT,
    ProfileData,
    build_persona_prompt,
    list_topic_groups,
    load_profile,
)
from personal_agent.contracts import KnowledgeVisibility
from personal_agent.knowledge.documents import parse_markdown_document
from personal_agent.knowledge.embedding import HashEmbeddingProvider
from personal_agent.knowledge.retrieval import PersonalKnowledgeService
from personal_agent.knowledge.store import KnowledgeStore

PROFILE_MARKDOWN = """---
source_id: profile
project: Personal
title: 个人身份与画像
visibility: private
profile: true
name: Wen
monogram: W
role: 后端与 Agent 方向开发者
github: https://github.com/bq-wen
greeting: 你好，我是 Wen 的 AI 数字分身。
style: 简洁、直接
covered_topics: [WenGraph, RAG]
public_summary: 个人数字分身身份与画像资料。
public_questions:
  - 你的知识覆盖哪些主题？
---
# Wen

后端与 Agent 方向开发者。自研 WenGraph 图运行时。"""


def _write_document(path: Path, *, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _indexed_store(tmp_path: Path) -> KnowledgeStore:
    store = KnowledgeStore(tmp_path / "knowledge.db")
    document_path = tmp_path / "profile.md"
    _write_document(document_path, content=PROFILE_MARKDOWN)
    service = PersonalKnowledgeService(store, HashEmbeddingProvider())
    service.index_document(parse_markdown_document(document_path))
    return store


def test_profile_document_parses_with_identity_fields(tmp_path: Path) -> None:
    path = tmp_path / "profile.md"
    path.write_text(PROFILE_MARKDOWN, encoding="utf-8")
    document = parse_markdown_document(path)

    assert document.metadata.profile is True
    assert document.metadata.name == "Wen"
    assert document.metadata.monogram == "W"
    assert document.metadata.covered_topics == ["WenGraph", "RAG"]
    assert document.metadata.public_questions == ["你的知识覆盖哪些主题？"]


def test_profile_document_requires_name(tmp_path: Path) -> None:
    from personal_agent.knowledge.documents import KnowledgeDocumentError

    path = tmp_path / "invalid-profile.md"
    path.write_text(
        """---
source_id: profile
project: Personal
title: 无名字身份
visibility: private
profile: true
public_summary: 缺失 name。
---
正文
""",
        encoding="utf-8",
    )
    try:
        parse_markdown_document(path)
    except KnowledgeDocumentError:
        return
    raise AssertionError("身份文档缺少 name 时应报错")


def test_load_profile_from_indexed_store(tmp_path: Path) -> None:
    store = _indexed_store(tmp_path)
    try:
        profile = load_profile(store)
        assert profile is not None
        assert profile.name == "Wen"
        assert profile.greeting == "你好，我是 Wen 的 AI 数字分身。"
        assert profile.covered_topics == ["WenGraph", "RAG"]
        assert load_profile(KnowledgeStore(tmp_path / "empty.db")) is None
    finally:
        store.close()


def test_build_persona_prompt_falls_back_without_profile() -> None:
    assert build_persona_prompt(None) == DEFAULT_PERSONA_PROMPT
    assert "本人实时在线" in DEFAULT_PERSONA_PROMPT


def test_build_persona_prompt_injects_identity() -> None:
    profile = ProfileData(
        name="Wen",
        monogram="W",
        role="后端与 Agent 方向开发者",
        style="简洁、直接",
        covered_topics=["WenGraph", "RAG"],
    )

    prompt = build_persona_prompt(profile)

    assert "你是Wen的 AI 数字分身" in prompt
    assert "后端与 Agent 方向开发者" in prompt
    assert "WenGraph、RAG" in prompt
    assert "简洁、直接" in prompt
    assert "绝不能补全或猜测" in prompt


def test_list_topic_groups_groups_by_project_with_questions(tmp_path: Path) -> None:
    store = _indexed_store(tmp_path)
    try:
        groups = list_topic_groups(store.list_sources())
    finally:
        store.close()

    assert len(groups) == 1
    assert groups[0].project == "Personal"
    assert groups[0].topics[0].questions == ["你的知识覆盖哪些主题？"]


def test_profile_source_is_visitor_safe_and_private(tmp_path: Path) -> None:
    store = _indexed_store(tmp_path)
    try:
        source = store.get_source("profile")
        assert source is not None
        assert source.visibility is KnowledgeVisibility.PRIVATE
        assert source.public_questions == ["你的知识覆盖哪些主题？"]
    finally:
        store.close()
