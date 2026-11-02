FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN pip install --no-cache-dir "uv==0.11.26"

COPY . /app
RUN uv sync --frozen --no-dev --package agent-runtime

EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/health/live', timeout=3)"
CMD ["uvicorn", "agent_runtime.main:app", "--host", "0.0.0.0", "--port", "8001"]
