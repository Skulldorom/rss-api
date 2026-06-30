# Multi-stage build for security and efficiency
FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.14-slim AS builder

# Copy uv binary from official image
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# Copy dependency manifests and install dependencies from lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Production stage
FROM python:3.14-slim AS production

# Create a non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy virtual environment from builder stage
COPY --from=builder /app/.venv /app/.venv

WORKDIR /app

# Copy only the necessary application files
COPY main.py ./

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 5000

# Container health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD /app/.venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health')"

# Start FastAPI app with uvicorn
CMD ["/app/.venv/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]