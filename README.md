# Personal Agent

Personal Agent is a chat-first, first-person AI digital twin for technical
interviews. It is an independent application built on the fixed WenGraph
submodule under `vendor/wengraph`.

## Current Stage

The first implementation increment establishes the application boundary,
knowledge-document contract, and FastAPI health endpoint. Retrieval, WenGraph
graph assembly, queue workers, and the React interface follow in later
increments.

## Development

Use Python 3.10 or newer. Install development dependencies in an isolated
environment, then run:

```bash
python3.10 -m pytest
```

The application intentionally has no model configuration or real knowledge
materials committed yet.
