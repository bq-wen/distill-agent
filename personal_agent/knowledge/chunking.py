"""Stable heading-aware Markdown chunking for the small personal knowledge corpus."""

import re

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def chunk_markdown(content: str, *, max_chars: int = 1_200) -> list[tuple[str | None, str]]:
    """Split Markdown by heading, then paragraphs, without silently dropping text."""

    if max_chars < 200:
        raise ValueError("max_chars 必须至少为 200")
    text = content.strip()
    if not text:
        return []

    sections: list[tuple[str | None, str]] = []
    matches = list(HEADING_RE.finditer(text))
    if not matches:
        sections.append((None, text))
    else:
        prefix = text[: matches[0].start()].strip()
        if prefix:
            sections.append((None, prefix))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections.append((match.group(2), text[match.start() : end].strip()))

    chunks: list[tuple[str | None, str]] = []
    for heading, section in sections:
        chunks.extend(_split_section(heading, section, max_chars))
    return chunks


def _split_section(heading: str | None, section: str, max_chars: int) -> list[tuple[str | None, str]]:
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n\s*\n", section) if paragraph.strip()]
    result: list[tuple[str | None, str]] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            if current:
                result.append((heading, current))
                current = ""
            result.extend(
                (heading, paragraph[start : start + max_chars]) for start in range(0, len(paragraph), max_chars)
            )
            continue
        candidate = paragraph if not current else f"{current}\n\n{paragraph}"
        if len(candidate) > max_chars:
            result.append((heading, current))
            current = paragraph
        else:
            current = candidate
    if current:
        result.append((heading, current))
    return result
