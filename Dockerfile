# Multi-stage build for security and efficiency
FROM python:3.12-slim AS builder

# Install uv (the package manager)
RUN pip install --upgrade pip && pip install uv

WORKDIR /app

# Copy only dependency manifest and install dependencies
COPY pyproject.toml ./
RUN uv pip install --system -r pyproject.toml

# Production stage
FROM python:3.12-slim AS production

# Create a non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

# Copy Python packages from builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Copy only the necessary application files
COPY app.py ./

# Change ownership to non-root user
RUN chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

EXPOSE 5000

# Start FastAPI app with uvicorn
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "5000"]