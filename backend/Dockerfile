# syntax=docker/dockerfile:1.7
# Build context: backend/.   docker build -t insightfulcell-api backend
FROM python:3.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first: this layer is rebuilt only when the lockfile changes.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

FROM python:3.13-slim
RUN useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY --from=build --chown=app /app/.venv ./.venv
COPY --chown=app alembic.ini ./
COPY --chown=app migrations ./migrations
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"
# Migrations are a separate deploy step: `docker run ... alembic upgrade head` as the owner role.
# The worker is this same image: `docker run --no-healthcheck ... python -m insightfulcell.worker`
# (the health check above probes the API port).
# Open event streams would otherwise hold shutdown for up to 5 minutes; clients resume with
# Last-Event-ID, so they are cut after 10 s.
CMD ["uvicorn", "insightfulcell.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--timeout-graceful-shutdown", "10"]
