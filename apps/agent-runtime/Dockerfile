FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PATH="/app/.venv/bin:$PATH"

ARG INSTALL_RETRIEVAL=1

RUN groupadd -r appuser && useradd -r -g appuser -d /app -s /sbin/nologin appuser && \
    pip install --no-cache-dir "uv==0.11.26"

COPY . /app
RUN uv sync --frozen --no-dev --package agent-runtime && \
    if [ "$INSTALL_RETRIEVAL" = "1" ]; then \
        uv pip install --python /app/.venv/bin/python --no-cache \
            --index-url https://download.pytorch.org/whl/cpu \
            "torch==2.12.0+cpu" && \
        uv pip install --python /app/.venv/bin/python --no-cache \
            "sentence-transformers==5.5.1" "transformers==5.10.2" "rank-bm25==0.2.2"; \
    fi && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/live', timeout=3)"
CMD ["uvicorn", "agent_runtime.main:app", "--host", "0.0.0.0", "--port", "8001"]
