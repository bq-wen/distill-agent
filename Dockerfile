# Build the React application first; browser tooling does not enter the runtime image.
FROM node:22-bookworm-slim AS frontend-build
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/app/models \
    PERSONAL_AGENT_DATA_DIR=/app/data \
    PERSONAL_AGENT_EMBEDDING_DEVICE=cpu
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY personal_agent/ ./personal_agent/
COPY vendor/wengraph/ ./vendor/wengraph/
COPY --from=frontend-build /build/frontend/dist ./frontend_dist/
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data /app/models && chown -R appuser:appuser /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
# One process is required because the bounded queue is in-memory.
CMD ["uvicorn", "personal_agent.api.bootstrap:create_production_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
