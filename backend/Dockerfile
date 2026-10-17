FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.15-slim
COPY --from=uv /uv /uvx /bin/
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
COPY alembic.ini ./
COPY alembic ./alembic
RUN uv sync --locked --no-dev && \
    useradd --create-home --uid 10001 app && \
    mkdir -p /var/lib/celery && chown app:app /var/lib/celery
USER app
EXPOSE 8000
CMD ["uv", "run", "--no-sync", "uvicorn", "cartalytix.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
