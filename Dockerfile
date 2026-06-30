# Multi-stage build for security and efficiency
FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.14-slim AS builder

# Copy uv binary from official image
COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# Copy only dependency manifest and install dependencies
COPY pyproject.toml ./
RUN uv pip install --system .

# Production stage
FROM python:3.14-slim AS production

# Create a non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy Python packages from builder stage
COPY --from=builder /usr/local/lib/python3.14/site-packages /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Copy only the necessary application files
COPY main.py ./

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 5000

# Health check using the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')" || exit 1

# Start FastAPI app with uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "5000"]