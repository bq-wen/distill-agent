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
    PERSONAL_AGENT_EMBEDDING_DEVICE=cpu \
    PERSONAL_AGENT_EMBEDDING_MODEL=/app/models/bge-small-zh-v1.5
WORKDIR /app
COPY requirements.txt ./
# Install the CPU wheel explicitly before the application requirements.  Without
# this pin pip resolves the CUDA distribution of torch through transformers.
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.7.1+cpu" \
    && pip install --no-cache-dir -r requirements.txt
COPY personal_agent/ ./personal_agent/
COPY vendor/wengraph/ ./vendor/wengraph/
COPY models/ ./models/
COPY --from=frontend-build /build/frontend/dist ./frontend_dist/
# Validate the baked embedding cache during the image build.  This prevents a
# release from silently shipping an image that can only start with internet.
RUN mkdir -p /app/data /app/models \
    && TRANSFORMERS_OFFLINE=1 python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('/app/models/bge-small-zh-v1.5', device='cpu')" \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
# One process is required because the bounded queue is in-memory.
CMD ["uvicorn", "personal_agent.api.bootstrap:create_production_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
