# Personal Agent

Personal Agent is a chat-first, first-person AI digital twin for technical
interviews. It is an independent application built on the fixed WenGraph
submodule under `vendor/wengraph`.

## Current Stage

The first two implementation increments establish the application boundary,
knowledge-document contract, local SQLite retrieval index, and FastAPI health
endpoint. WenGraph graph assembly, queue workers, and the React interface
follow in later increments.

## Development

Use Python 3.10 or newer. Install development dependencies in an isolated
environment, then run:

```bash
python3.10 -m pytest
```

The application intentionally has no model configuration or real knowledge
materials committed yet.

## Knowledge Index

Markdown source documents start with YAML front matter containing `source_id`,
`project`, `title`, `visibility`, and an approved `public_summary`. Build a
local index with a cached Sentence Transformers model:

```bash
python -m personal_agent.knowledge.cli knowledge/ --database data/knowledge.db
```

For deterministic contract testing without downloading a model, add
`--hash-embedding`. Hash vectors are not appropriate for production semantic
retrieval.
