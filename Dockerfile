FROM python:3
WORKDIR /app
COPY app.py .
COPY pyproject.toml uv.lock ./
# Install uv
RUN pip install uv
# Install dependencies using uv
RUN uv pip install --system --group default
CMD ["python", "app.py"]