FROM python:3.11-slim

# Install uv (the package manager)
RUN pip install --upgrade pip && pip install uv

WORKDIR /app

# Copy only dependency manifest and install dependencies
COPY pyproject.toml ./
RUN uv pip install -r pyproject.toml

# Copy rest of the application code
COPY . .

EXPOSE 5000

# Set environment variable for Flask
ENV FLASK_RUN_HOST=0.0.0.0

# Start Flask app
CMD ["flask", "run"]