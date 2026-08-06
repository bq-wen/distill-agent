# Distill Agent Repository Instructions

`personal_agent` is an application layer built on the fixed `vendor/wengraph`
submodule. Do not edit framework source from this repository.

- Keep HTTP concerns in `personal_agent.api`; keep agent and knowledge behavior
  in `personal_agent.application` and `personal_agent.knowledge`.
- Use shared Pydantic contracts at API and persistence boundaries. Do not expose
  raw Markdown paths, private content, or SQLite rows from API handlers.
- Knowledge documents may include private content, but public citations must be
  assembled solely from approved public metadata.
- Use Python 3.12. Run tests with `python3.12 -m pytest`.
- Keep model credentials in environment files only; never commit real keys.
