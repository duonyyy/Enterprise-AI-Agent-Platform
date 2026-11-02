FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

RUN pip install --no-cache-dir "uv==0.11.26"

COPY . /app
RUN uv sync --frozen --no-dev --package jobs-worker

CMD ["python", "-m", "jobs_worker.main"]
