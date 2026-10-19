# Build context: repository root.
FROM python:3.12-slim-bookworm AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never UV_PYTHON_PREFERENCE=only-system UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

FROM python:3.12-slim-bookworm
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home appuser
COPY --from=builder /app/.venv /app/.venv
COPY services/__init__.py services/__init__.py
COPY services/mock-store services/mock-store
USER appuser
EXPOSE 8001
CMD ["python", "-m", "uvicorn", "services.mock-store.main:app", "--host", "0.0.0.0", "--port", "8001"]
