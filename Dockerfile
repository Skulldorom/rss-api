FROM python:3
WORKDIR /app
COPY app.py .
COPY pyproject.toml uv.lock ./
# Install uv
RUN pip install uv
# Install dependencies using uv
RUN uv pip install --system --requirement pyproject.toml --requirement uv.lock
CMD ["python", "app.py"]